import Combine
import Foundation
import Security

private enum SettingsKeychain {
    private static let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "org.autopoliv.PlantWatch.connection",
        kSecAttrAccount as String: "backend"
    ]

    static func load() throws -> ConnectionSettings? {
        var request = query
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else {
            throw PlantAPIError.message("Не удалось открыть настройки. Разблокируй часы и перезапусти приложение.")
        }
        return try JSONDecoder().decode(ConnectionSettings.self, from: data)
    }

    static func save(_ settings: ConnectionSettings) throws {
        let values: [String: Any] = [
            kSecValueData as String: try JSONEncoder().encode(settings),
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        ]
        var status = SecItemUpdate(query as CFDictionary, values as CFDictionary)
        if status == errSecItemNotFound {
            status = SecItemAdd(query.merging(values) { _, new in new } as CFDictionary, nil)
        }
        guard status == errSecSuccess else {
            throw PlantAPIError.message("Не удалось сохранить настройки в Keychain. Разблокируй часы.")
        }
    }

    static func remove() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw PlantAPIError.message("Не удалось удалить настройки из Keychain.")
        }
    }
}

@MainActor
final class WatchStore: ObservableObject {
    @Published var address = "http://10.0.0.20:8000"
    @Published var username = "admin"
    @Published var password = ""
    @Published var device = ""
    @Published private(set) var devices: [String] = []
    @Published private(set) var settings: ConnectionSettings?
    @Published private(set) var snapshot: PlantSnapshot?
    @Published private(set) var isDemo = false
    @Published private(set) var isLoading = false
    @Published private(set) var connectionError: String?
    @Published private(set) var settingsError: String?
    @Published private(set) var revision = 0

    private let api = PlantAPI()
    private var connectionTask: Task<Void, Never>?
    private var active = false
    private var pendingLoads = 0

    init() {
        do {
            settings = try SettingsKeychain.load()
            if let settings {
                address = settings.address
                username = settings.username
                password = settings.password
                device = settings.device
            }
        } catch { settingsError = error.localizedDescription }
    }

    var pollingEnabled: Bool { active && !isDemo && settings != nil }

    func setActive(_ value: Bool) {
        active = value
        if !value { connectionTask?.cancel() }
        revision += 1
    }

    func connect() {
        connectionTask?.cancel()
        connectionTask = Task { await connectDraft() }
    }

    private func connectDraft() async {
        settingsError = nil
        var candidate = ConnectionSettings(address: address.trimmingCharacters(in: .whitespacesAndNewlines),
                                           username: username, password: password,
                                           device: device.trimmingCharacters(in: .whitespacesAndNewlines))
        revision += 1
        beginLoad()
        defer { endLoad() }
        do {
            _ = try candidate.baseURL()
            let found = try await api.devices(settings: candidate)
            try Task.checkCancellation()
            devices = found
            if candidate.device.isEmpty && found.count == 1 { candidate.device = found[0]; device = found[0] }
            guard !found.isEmpty else {
                throw PlantAPIError.message("Сервер доступен, но растений пока нет. Дождись телеметрии контроллера.")
            }
            guard !candidate.device.isEmpty, found.contains(candidate.device) else {
                throw PlantAPIError.message("Выбери растение из списка и нажми «Подключить».")
            }
            let data = try await api.snapshot(settings: candidate)
            try Task.checkCancellation()
            try SettingsKeychain.save(candidate)
            settings = candidate
            snapshot = data
            connectionError = nil
            isDemo = false
        } catch is CancellationError {
            return
        } catch {
            if !Task.isCancelled { settingsError = Self.message(for: error) }
        }
        revision += 1
    }

    func poll() async {
        while !Task.isCancelled && pollingEnabled {
            await refresh()
            do { try await Task.sleep(nanoseconds: 30_000_000_000) }
            catch { return }
        }
    }

    func refresh() async {
        guard !isLoading, pollingEnabled, let settings else { return }
        let version = revision
        beginLoad()
        defer { endLoad() }
        do {
            let data = try await api.snapshot(settings: settings)
            guard !Task.isCancelled, version == revision, pollingEnabled else { return }
            snapshot = data
            connectionError = nil
        } catch {
            guard !Task.isCancelled, version == revision else { return }
            connectionError = Self.message(for: error)
        }
    }

    func showDemo() {
        connectionTask?.cancel()
        isDemo = true
        revision += 1
    }

    func showLive() {
        isDemo = false
        revision += 1
    }

    func forget() {
        connectionTask?.cancel()
        do {
            try SettingsKeychain.remove()
            settings = nil
            snapshot = nil
            password = ""
            device = ""
            devices = []
            isDemo = false
            connectionError = nil
            settingsError = nil
            revision += 1
        } catch { settingsError = error.localizedDescription }
    }

    private func beginLoad() {
        pendingLoads += 1
        isLoading = true
    }

    private func endLoad() {
        pendingLoads -= 1
        isLoading = pendingLoads > 0
    }

    private static func message(for error: Error) -> String {
        if let error = error as? PlantAPIError { return error.localizedDescription }
        if let error = error as? URLError {
            switch error.code {
            case .notConnectedToInternet, .cannotConnectToHost, .cannotFindHost, .timedOut:
                return "Нет связи с сервером. Проверь адрес, порт и сеть часов. Повторим автоматически."
            case .appTransportSecurityRequiresSecureConnection:
                return "Соединение заблокировано watchOS. Используй HTTPS; HTTP по IP требует watchOS 26+."
            case .secureConnectionFailed, .serverCertificateUntrusted, .serverCertificateHasBadDate:
                return "Не удалось проверить HTTPS-сертификат сервера."
            default: break
            }
        }
        return "Не удалось получить данные. Повторим автоматически."
    }
}
