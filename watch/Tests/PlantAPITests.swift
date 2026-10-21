import Foundation
import XCTest
@testable import PlantWatchCore

private final class StubProtocol: URLProtocol {
    static var handler: ((URLRequest) throws -> (Int, String))!
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            let (status, json) = try Self.handler(request)
            let response = HTTPURLResponse(url: request.url!, statusCode: status,
                                           httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: Data(json.utf8))
            client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() {}
}

final class PlantAPITests: XCTestCase {
    private let settings = ConnectionSettings(address: "http://10.0.0.20:8000", username: "admin",
                                             password: "fixture-only", device: "цветок & 2")
    private var session: URLSession!
    private var api: PlantAPI!

    override func setUp() {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubProtocol.self]
        session = URLSession(configuration: config)
        api = PlantAPI(session: session)
    }
    override func tearDown() {
        session.invalidateAndCancel()
        api = nil
        StubProtocol.handler = nil
    }

    func testReadsAuthenticatedStateAndEncodesDeviceWithoutInjectingQuery() async throws {
        StubProtocol.handler = { request in
            XCTAssertEqual(request.httpMethod, "GET")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"),
                           "Basic " + Data("admin:fixture-only".utf8).base64EncodedString())
            let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!.queryItems!
            XCTAssertEqual(query.filter { $0.name == "device" }.map(\.value), ["цветок & 2"])
            XCTAssertEqual(query.count, 1)
            switch request.url!.path {
            case "/web/api/state": return (200, Self.state(events: #"[{"ts":900,"type":"water"}]"#))
            case "/web/api/forecast": return (200, #"{"status":"ok","hours_to_threshold":8,"next_water_ts":29800}"#)
            default: XCTFail("Unexpected endpoint"); return (404, "{}")
            }
        }
        let result = try await api.snapshot(settings: settings)
        XCTAssertEqual(result.soil, 46)
        XCTAssertEqual(result.state.latest?.air_hum, 55)
        XCTAssertEqual(result.lastWater, 900)
        XCTAssertEqual(result.hoursToWater(at: result.receivedAt), 8)
    }

    func testOldWateringIsLoadedWhenTwentyEventWindowHasNoWater() async throws {
        StubProtocol.handler = { request in
            switch request.url!.path {
            case "/web/api/state": return (200, Self.state(events: #"[{"ts":990,"type":"boot"}]"#))
            case "/web/api/forecast": return (503, "{}")
            case "/web/api/waterings": return (200, #"[{"ts":100},{"ts":700},{"ts":200}]"#)
            default: XCTFail("Unexpected endpoint"); return (404, "{}")
            }
        }
        let result = try await api.snapshot(settings: settings)
        XCTAssertEqual(result.lastWater, 700)
        XCTAssertNil(result.forecast)
        XCTAssertEqual(result.soil, 46) // Forecast failure must not hide sensor readings.
    }

    func testUnauthorizedNeverTurnsIntoDemoData() async throws {
        StubProtocol.handler = { _ in (401, #"{"detail":"auth required"}"#) }
        do {
            _ = try await api.snapshot(settings: settings)
            XCTFail("Expected unauthorized")
        } catch PlantAPIError.unauthorized {} catch { XCTFail("Unexpected error: \(error)") }
    }

    func testNullSensorsAndInsufficientForecast() async throws {
        StubProtocol.handler = { request in
            switch request.url!.path {
            case "/web/api/state": return (200, #"{"now":1000,"demo":true,"latest":null,"calibration":null,"pending":null,"events":[]}"#)
            case "/web/api/forecast": return (200, #"{"status":"insufficient_data"}"#)
            default: return (200, "[]")
            }
        }
        let result = try await api.snapshot(settings: settings)
        XCTAssertNil(result.soil)
        XCTAssertNil(result.lastWater)
        XCTAssertNil(result.hoursToWater(at: result.receivedAt))
        XCTAssertFalse(result.isFresh(at: result.receivedAt))
        XCTAssertTrue(result.state.demo)
    }

    func testStalenessUsesServerClockAndAdvancesWithoutNetwork() throws {
        let state = try JSONDecoder().decode(PlantState.self, from: Data(Self.state(events: "[]").utf8))
        let localDate = Date(timeIntervalSince1970: 8_000_000)
        let result = PlantSnapshot(state: state, forecast: nil, lastWater: nil,
                                   historyUnavailable: false, receivedAt: localDate)
        XCTAssertTrue(result.isFresh(at: localDate))
        XCTAssertFalse(result.isFresh(at: localDate.addingTimeInterval(111)))
        XCTAssertEqual(result.serverTime(at: localDate.addingTimeInterval(60)), 1060)
    }

    func testInvalidSoilSensorDoesNotShowPercentageAndAcceptsBooleanFlag() throws {
        let json = Self.state(events: "[]").replacingOccurrences(of: "\"soil_ok\":1", with: "\"soil_ok\":false")
        let state = try JSONDecoder().decode(PlantState.self, from: Data(json.utf8))
        let result = PlantSnapshot(state: state, forecast: nil, lastWater: nil,
                                   historyUnavailable: false, receivedAt: Date())
        XCTAssertNil(result.soil)
        XCTAssertEqual(result.state.latest?.temp, 24)
    }

    func testAddressValidationRejectsEmbeddedCredentialsAndHTTPDNS() throws {
        for address in ["http://user:pass@10.0.0.20", "file:///tmp/data", "http://example.com",
                        "http://10.0.0.20?token=x", "http://10.0.0.20/#tab", "10.0.0.20"] {
            var candidate = settings
            candidate.address = address
            XCTAssertThrowsError(try candidate.baseURL(), address)
        }
        XCTAssertEqual(try settings.baseURL().host, "10.0.0.20")
        var https = settings
        https.address = "https://plants.example.com/prefix/"
        XCTAssertEqual(try https.baseURL().path, "/prefix")
    }

    func testRedirectIsRejectedInsteadOfForwardingPassword() {
        let delegate = RejectRedirects()
        let request = URLRequest(url: URL(string: "https://other.example.com")!)
        let response = HTTPURLResponse(url: URL(string: "http://10.0.0.20:8000")!,
                                       statusCode: 302, httpVersion: nil, headerFields: nil)!
        delegate.urlSession(session, task: session.dataTask(with: request),
                            willPerformHTTPRedirection: response, newRequest: request) { redirected in
            XCTAssertNil(redirected)
        }
    }

    func testHTMLLoginPageIsNotTreatedAsSuccessfulAPI() async throws {
        StubProtocol.handler = { _ in (200, "<html>Login</html>") }
        do {
            _ = try await api.devices(settings: settings)
            XCTFail("Expected invalid API response")
        } catch PlantAPIError.message {} catch { XCTFail("Unexpected error: \(error)") }
    }

    private static func state(events: String) -> String {
        """
        {"now":1000,"demo":false,"latest":{"ts":990,"temp":24,"air_hum":55,"soil_pct":46,"soil_ok":1,"soil_dry_d0":0},"calibration":{"target_low":30,"target_high":80},"pending":null,"events":\(events)}
        """
    }
}
