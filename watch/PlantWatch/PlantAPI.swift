import Foundation

struct ConnectionSettings: Codable, Equatable {
    var address: String
    var username: String
    var password: String
    var device: String

    func baseURL() throws -> URL {
        let text = address.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let parts = URLComponents(string: text),
              let scheme = parts.scheme?.lowercased(), ["https", "http"].contains(scheme),
              let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil,
              parts.query == nil, parts.fragment == nil,
              parts.port.map({ (1...65535).contains($0) }) ?? true,
              let url = parts.url else {
            throw PlantAPIError.message("Укажи адрес http://IP:8000 или https://домен без логина и параметров.")
        }
        // ATS keeps HTTPS enforced for public DNS names. HTTP is limited to the
        // IP/local-host addresses supported by NSAllowsLocalNetworking (watchOS 26+).
        let ipv4 = host.split(separator: ".", omittingEmptySubsequences: false)
        let isIPv4 = ipv4.count == 4 && ipv4.allSatisfy { UInt8($0) != nil }
        let isLocal = host.hasSuffix(".local") || !host.contains(".")
        if scheme == "http" && !(isIPv4 || host.contains(":") || isLocal) {
            throw PlantAPIError.message("Для доменного имени нужен HTTPS. HTTP доступен по IP или локальному имени.")
        }
        guard !username.isEmpty, !username.contains(":"), !password.isEmpty,
              !username.contains(where: { $0.isNewline }),
              !password.contains(where: { $0.isNewline }) else {
            throw PlantAPIError.message("Введи логин и пароль от сайта. В логине не должно быть двоеточия.")
        }
        return url
    }
}

enum PlantAPIError: Error, LocalizedError {
    case unauthorized
    case http(Int)
    case message(String)

    var errorDescription: String? {
        switch self {
        case .unauthorized: return "Неверный логин или пароль от веб-дашборда."
        case .http(let status): return "Сервер ответил HTTP \(status). Проверь адрес и доступность сервиса."
        case .message(let text): return text
        }
    }
}

// SQLite serializes flags as 0/1; also accept JSON booleans for compatibility.
struct ServerFlag: Decodable {
    let value: Bool
    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let boolean = try? container.decode(Bool.self) { value = boolean }
        else { value = try container.decode(Int.self) != 0 }
    }
}

struct PlantMeasurement: Decodable {
    let ts: Double
    let temp: Double?
    let air_hum: Double?
    let soil_pct: Double?
    let soil_ok: ServerFlag
    let soil_dry_d0: ServerFlag?
}

struct PlantEvent: Decodable {
    let ts: Double
    let type: String
}

struct PlantCalibration: Decodable {
    let target_low: Double?
    let target_high: Double?
}

struct PlantPending: Decodable { let seconds: Double }
struct PlantState: Decodable {
    let now: Double
    let demo: Bool
    let latest: PlantMeasurement?
    let calibration: PlantCalibration?
    let pending: PlantPending?
    let events: [PlantEvent]
}

struct PlantForecast: Decodable {
    let status: String
    let hours_to_threshold: Double?
    let next_water_ts: Double?
}

struct PlantWatering: Decodable { let ts: Double }

struct PlantSnapshot {
    let state: PlantState
    let forecast: PlantForecast?
    let lastWater: Double?
    let historyUnavailable: Bool
    let receivedAt: Date

    func serverTime(at date: Date) -> Double {
        state.now + max(0, date.timeIntervalSince(receivedAt))
    }

    func isFresh(at date: Date) -> Bool {
        guard let measurement = state.latest else { return false }
        let age = serverTime(at: date) - measurement.ts
        return age >= -60 && age < 120
    }

    var soil: Double? {
        guard state.latest?.soil_ok.value == true else { return nil }
        return state.latest?.soil_pct
    }

    var threshold: Double { state.calibration?.target_low ?? 30 }
    var high: Double { min(state.calibration?.target_high ?? 70, 70) }

    func hoursToWater(at date: Date) -> Double? {
        guard isFresh(at: date), forecast?.status == "ok",
              let hours = forecast?.hours_to_threshold, hours.isFinite else { return nil }
        // A server forecast ages even while the next request is in flight.
        return max(0, hours - max(0, date.timeIntervalSince(receivedAt)) / 3600)
    }
}

final class RejectRedirects: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        // Never forward the Basic password to a redirect target or follow an HTTPS downgrade.
        completionHandler(nil)
    }
}

final class PlantAPI {
    private let session: URLSession

    init(session: URLSession? = nil) {
        if let session { self.session = session; return }
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 12
        config.timeoutIntervalForResource = 20
        config.urlCache = nil
        config.httpCookieStorage = nil
        config.urlCredentialStorage = nil
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        self.session = URLSession(configuration: config, delegate: RejectRedirects(), delegateQueue: nil)
    }

    deinit { session.invalidateAndCancel() }

    func get<T: Decodable>(_ path: String, settings: ConnectionSettings,
                           query: [URLQueryItem] = []) async throws -> T {
        let base = try settings.baseURL()
        var parts = URLComponents(url: base.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        parts.queryItems = query.isEmpty ? nil : query
        var request = URLRequest(url: parts.url!)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let credentials = Data("\(settings.username):\(settings.password)".utf8).base64EncodedString()
        request.setValue("Basic \(credentials)", forHTTPHeaderField: "Authorization")
        let (data, response) = try await session.data(for: request)
        try Task.checkCancellation()
        guard let response = response as? HTTPURLResponse else {
            throw PlantAPIError.message("Некорректный ответ сервера.")
        }
        if response.statusCode == 401 || response.statusCode == 403 { throw PlantAPIError.unauthorized }
        guard (200...299).contains(response.statusCode) else { throw PlantAPIError.http(response.statusCode) }
        do { return try JSONDecoder().decode(T.self, from: data) }
        catch { throw PlantAPIError.message("Ответ не похож на API растения. Проверь адрес сервера.") }
    }

    func devices(settings: ConnectionSettings) async throws -> [String] {
        try await get("web/api/devices", settings: settings)
    }

    func snapshot(settings: ConnectionSettings) async throws -> PlantSnapshot {
        let query = [URLQueryItem(name: "device", value: settings.device)]
        let state: PlantState = try await get("web/api/state", settings: settings, query: query)
        let receivedAt = Date()
        let forecast: PlantForecast? = try? await get("web/api/forecast", settings: settings, query: query)
        var lastWater = state.events.filter { $0.type == "water" }.map(\.ts).max()
        var historyUnavailable = false
        if lastWater == nil {
            // /state contains only 20 events, so a reboot/chat burst can hide the last watering.
            // The existing history API retains up to ten years; only query it when needed.
            do {
                let history: [PlantWatering] = try await get("web/api/waterings", settings: settings,
                    query: query + [URLQueryItem(name: "hours", value: "87600")])
                lastWater = history.map(\.ts).max()
            } catch {
                historyUnavailable = true
            }
        }
        try Task.checkCancellation()
        return PlantSnapshot(state: state, forecast: forecast, lastWater: lastWater,
                             historyUnavailable: historyUnavailable, receivedAt: receivedAt)
    }
}
