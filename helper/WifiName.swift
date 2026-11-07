// Small background app: macOS shows the Wi-Fi name only to an app the user allowed Location for.
// It reads the network name (never coordinates) and writes it to one local file for the widget.
import AppKit
import CoreLocation
import CoreWLAN

let outFile = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent(".config/mac_monitor_wifi.json")

final class Helper: NSObject, CLLocationManagerDelegate {
    let manager = CLLocationManager()
    let parent: Int32

    init(parent: Int32) {
        self.parent = parent
        super.init()
        manager.delegate = self
    }

    func locationManagerDidChangeAuthorization(_ m: CLLocationManager) {
        if m.authorizationStatus == .notDetermined { m.requestAlwaysAuthorization() }
        tick()
    }

    func tick() {
        // The widget owns this helper: when the widget is gone, stop.
        if parent > 0 && kill(parent, 0) != 0 { exit(0) }
        let auth: String
        switch manager.authorizationStatus {
        case .notDetermined: auth = "asking"
        case .authorizedAlways, .authorized: auth = "allowed"
        default: auth = "denied"
        }
        let name = CWWiFiClient.shared().interface()?.ssid() ?? ""
        let payload: [String: Any] = ["auth": auth, "ssid": name, "ts": Date().timeIntervalSince1970,
                                      "services": CLLocationManager.locationServicesEnabled(),
                                      "raw": manager.authorizationStatus.rawValue]
        if let data = try? JSONSerialization.data(withJSONObject: payload) {
            try? data.write(to: outFile, options: .atomic)
        }
    }
}

var parent: Int32 = 0
if let i = CommandLine.arguments.firstIndex(of: "--parent"), i + 1 < CommandLine.arguments.count {
    parent = Int32(CommandLine.arguments[i + 1]) ?? 0
}
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let helper = Helper(parent: parent)
helper.tick()
Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { _ in helper.tick() }
app.run()
