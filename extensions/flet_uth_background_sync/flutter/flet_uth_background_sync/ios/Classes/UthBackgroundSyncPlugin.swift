import CryptoKit
import Flutter
import UIKit
import UserNotifications

public final class UthBackgroundSyncPlugin: NSObject, FlutterPlugin, UNUserNotificationCenterDelegate {
    private static let channelName = "com.uthelper/background_sync"
    private static let requestPrefix = "uth.deadline."
    private static let immediatePrefix = "uth.immediate."
    private static let settingsKey = "uth.notification.settings"
    private static let receiptsKey = "uth.notification.receipts"

    private let center = UNUserNotificationCenter.current()
    private let defaults = UserDefaults.standard

    public static func register(with registrar: FlutterPluginRegistrar) {
        let channel = FlutterMethodChannel(
            name: channelName,
            binaryMessenger: registrar.messenger()
        )
        let instance = UthBackgroundSyncPlugin()
        registrar.addMethodCallDelegate(instance, channel: channel)
        instance.center.delegate = instance
    }

    public func handle(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
        switch call.method {
        case "configure":
            let arguments = call.arguments as? [String: Any]
            let settings = arguments?["settings"] as? [String: Any] ?? [:]
            defaults.set(settings, forKey: Self.settingsKey)
            center.requestAuthorization(options: [.alert, .badge, .sound]) { granted, error in
                self.finish(result, [
                    "enabled": self.bool(settings["enabled"], default: true),
                    "permission_granted": granted,
                    "scheduled": 0,
                    "cancelled": 0,
                    "error": error?.localizedDescription ?? "",
                ])
            }

        case "set_credentials":
            // iOS schedules from the foreground snapshot and never persists Moodle tokens.
            result(nil)

        case "import_activities":
            let arguments = call.arguments as? [String: Any]
            let activities = arguments?["activities"] as? [[String: Any]] ?? []
            reconcile(activities: activities, allowImmediate: true, result: result)

        case "get_cached_activities":
            result([])

        case "show_notification":
            let arguments = call.arguments as? [String: Any] ?? [:]
            let content = UNMutableNotificationContent()
            content.title = text(arguments["title"], default: "UTHelper")
            content.body = text(arguments["body"])
            content.sound = .default
            content.userInfo = ["url": text(arguments["payload"])]
            let identifier = Self.immediatePrefix + String(
                integer(arguments["notification_id"], default: Int.random(in: 1...Int.max))
            )
            center.add(
                UNNotificationRequest(identifier: identifier, content: content, trigger: nil)
            ) { error in
                self.finish(result, error == nil)
            }

        case "get_diagnostics":
            diagnostics(result: result)

        case "reconcile_cached":
            center.getPendingNotificationRequests { requests in
                let pending = requests.filter { $0.identifier.hasPrefix(Self.requestPrefix) }.count
                self.finish(result, [
                    "desired": pending,
                    "scheduled": 0,
                    "cancelled": 0,
                    "delivered": 0,
                ])
            }

        case "request_exact_alarm_access":
            // Calendar notifications do not have Android's exact-alarm permission.
            result(true)

        case "schedule_periodic":
            result(["scheduled": false, "reason": "ios_foreground_snapshot_only"])

        case "sync_now":
            result("unsupported_on_ios")

        case "cancel_periodic":
            result(nil)

        case "install_update":
            result(["status": "unavailable_on_ios"])

        case "logout":
            clearNotifications()
            defaults.removeObject(forKey: Self.settingsKey)
            defaults.removeObject(forKey: Self.receiptsKey)
            result(nil)

        default:
            result(FlutterMethodNotImplemented)
        }
    }

    private func reconcile(
        activities: [[String: Any]],
        allowImmediate: Bool,
        result: @escaping FlutterResult
    ) {
        let settings = defaults.dictionary(forKey: Self.settingsKey) ?? [:]
        guard bool(settings["enabled"], default: true) else {
            clearNotifications()
            result(["imported": activities.count, "scheduled": 0, "cancelled": 0, "delivered": 0])
            return
        }

        let now = Date()
        let milestones = intArray(settings["countdown_minutes"])
            .filter { $0 > 0 }
            .reduce(into: Set<Int>()) { $0.insert($1) }
            .sorted()
        var desired: [String: UNNotificationRequest] = [:]
        var immediate: [UNNotificationRequest] = []
        var receipts = Set(defaults.stringArray(forKey: Self.receiptsKey) ?? [])
        var seenScheduleKeys = Set<String>()

        for activity in activities {
            guard accepts(activity, settings: settings),
                  let deadline = parseDate(activity["deadline"]),
                  deadline > now else { continue }

            let key = activityKey(activity)
            let title = text(activity["title"], default: "Hoạt động sắp đến hạn")
            let course = text(activity["course_name"] ?? activity["course"])
            let url = text(activity["url"])
            let remainingMinutes = deadline.timeIntervalSince(now) / 60.0
            var crossedMilestone: Int?

            for milestone in milestones {
                let original = deadline.addingTimeInterval(TimeInterval(-milestone * 60))
                let scheduled = moveOutOfDnd(original, deadline: deadline, settings: settings)
                if scheduled > now && scheduled < deadline {
                    let scheduleKey = "\(key)|\(scheduled.timeIntervalSince1970)"
                    if seenScheduleKeys.contains(scheduleKey) { continue }
                    seenScheduleKeys.insert(scheduleKey)
                    let identifier = Self.requestPrefix + stableIdentifier("\(key)|\(milestone)")
                    let content = notificationContent(
                        title: title,
                        course: course,
                        milestone: milestone,
                        url: url,
                        activityKey: key
                    )
                    let components = Calendar.current.dateComponents(
                        [.year, .month, .day, .hour, .minute, .second],
                        from: scheduled
                    )
                    desired[identifier] = UNNotificationRequest(
                        identifier: identifier,
                        content: content,
                        trigger: UNCalendarNotificationTrigger(dateMatching: components, repeats: false)
                    )
                } else if remainingMinutes <= Double(milestone), crossedMilestone == nil {
                    crossedMilestone = milestone
                }
            }

            if allowImmediate, let milestone = crossedMilestone {
                let receipt = "\(key)|\(deadline.timeIntervalSince1970)|\(milestone)"
                if !receipts.contains(receipt) {
                    let identifier = Self.immediatePrefix + stableIdentifier(receipt)
                    immediate.append(
                        UNNotificationRequest(
                            identifier: identifier,
                            content: notificationContent(
                                title: title,
                                course: course,
                                milestone: milestone,
                                url: url,
                                activityKey: key
                            ),
                            trigger: nil
                        )
                    )
                    receipts.insert(receipt)
                }
            }
        }

        center.getPendingNotificationRequests { existing in
            let oldIdentifiers = Set(
                existing
                    .map(\.identifier)
                    .filter { $0.hasPrefix(Self.requestPrefix) }
            )
            let desiredIdentifiers = Set(desired.keys)
            let removed = oldIdentifiers.subtracting(desiredIdentifiers)
            if !removed.isEmpty {
                self.center.removePendingNotificationRequests(withIdentifiers: Array(removed))
            }

            let group = DispatchGroup()
            let lock = NSLock()
            var failures: [String] = []
            for request in Array(desired.values) + immediate {
                group.enter()
                self.center.add(request) { error in
                    if let error {
                        lock.lock()
                        failures.append(error.localizedDescription)
                        lock.unlock()
                    }
                    group.leave()
                }
            }
            group.notify(queue: .main) {
                if failures.isEmpty {
                    self.defaults.set(Array(Array(receipts).suffix(1000)), forKey: Self.receiptsKey)
                }
                result([
                    "imported": activities.count,
                    "authoritative": true,
                    "scheduled": desired.count,
                    "cancelled": removed.count,
                    "delivered": failures.isEmpty ? immediate.count : 0,
                    "failed": failures.count,
                    "errors": failures,
                ])
            }
        }
    }

    private func accepts(_ activity: [String: Any], settings: [String: Any]) -> Bool {
        let muted = Set(stringArray(settings["muted_courses"]).map {
            $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        })
        let course = text(activity["course_name"] ?? activity["course"])
            .trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if muted.contains(course) {
            return false
        }
        let status = text(activity["submission_status"], default: "unknown").lowercased()
        if bool(settings["ignore_submitted"], default: true),
           ["submitted", "graded", "đã nộp", "đã chấm"].contains(status) {
            return false
        }
        let allowedTypes = Set(stringArray(settings["notify_types"]))
        let eventType = text(activity["event_type"] ?? activity["type"])
        return allowedTypes.isEmpty || eventType.isEmpty || allowedTypes.contains(eventType)
    }

    private func moveOutOfDnd(_ date: Date, deadline: Date, settings: [String: Any]) -> Date {
        guard bool(settings["dnd_enabled"], default: false) else { return date }
        let start = integer(settings["dnd_start"], default: 22)
        let end = integer(settings["dnd_end"], default: 7)
        if start == end { return deadline }
        let hour = Calendar.current.component(.hour, from: date)
        let quiet = start > end ? (hour >= start || hour < end) : (hour >= start && hour < end)
        guard quiet else { return date }
        var components = Calendar.current.dateComponents([.year, .month, .day], from: date)
        components.hour = end
        components.minute = 0
        components.second = 0
        var candidate = Calendar.current.date(from: components) ?? date
        if start > end && hour >= start {
            candidate = Calendar.current.date(byAdding: .day, value: 1, to: candidate) ?? candidate
        }
        return min(candidate, deadline)
    }

    private func notificationContent(
        title: String,
        course: String,
        milestone: Int,
        url: String,
        activityKey: String
    ) -> UNMutableNotificationContent {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = course.isEmpty ? milestoneText(milestone) : "\(course) - \(milestoneText(milestone))"
        content.sound = .default
        content.userInfo = ["url": url, "activity_key": activityKey, "milestone": milestone]
        return content
    }

    private func milestoneText(_ minutes: Int) -> String {
        if minutes % 1440 == 0 { return "Còn \(minutes / 1440) ngày" }
        if minutes % 60 == 0 { return "Còn \(minutes / 60) giờ" }
        return "Còn \(minutes) phút"
    }

    private func activityKey(_ activity: [String: Any]) -> String {
        for name in ["activity_key", "id", "activity_id", "url"] {
            let value = text(activity[name])
            if !value.isEmpty { return value }
        }
        return "\(text(activity["course_id"]))|\(text(activity["title"]))"
    }

    private func parseDate(_ value: Any?) -> Date? {
        if let value = value as? Date { return value }
        if let value = value as? NSNumber {
            let raw = value.doubleValue
            return Date(timeIntervalSince1970: raw > 10_000_000_000 ? raw / 1000.0 : raw)
        }
        guard let raw = value as? String, !raw.isEmpty else { return nil }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = iso.date(from: raw) { return date }
        iso.formatOptions = [.withInternetDateTime]
        if let date = iso.date(from: raw) { return date }
        let local = DateFormatter()
        local.locale = Locale(identifier: "en_US_POSIX")
        local.timeZone = .current
        for format in ["yyyy-MM-dd'T'HH:mm:ss.SSSSSS", "yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd HH:mm:ss"] {
            local.dateFormat = format
            if let date = local.date(from: raw) { return date }
        }
        return nil
    }

    private func stableIdentifier(_ value: String) -> String {
        SHA256.hash(data: Data(value.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    private func diagnostics(result: @escaping FlutterResult) {
        center.getNotificationSettings { settings in
            self.center.getPendingNotificationRequests { requests in
                let pending = requests.filter { $0.identifier.hasPrefix(Self.requestPrefix) }.count
                self.finish(result, [
                    "worker_backend": "ios_user_notifications",
                    "permission_status": settings.authorizationStatus.rawValue,
                    "exact_alarm_allowed": true,
                    "pending_reminders": pending,
                    "credential_available": false,
                ])
            }
        }
    }

    private func clearNotifications() {
        center.getPendingNotificationRequests { requests in
            let identifiers = requests.map(\.identifier).filter {
                $0.hasPrefix(Self.requestPrefix) || $0.hasPrefix(Self.immediatePrefix)
            }
            self.center.removePendingNotificationRequests(withIdentifiers: identifiers)
            self.center.removeDeliveredNotifications(withIdentifiers: identifiers)
        }
    }

    public func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        if #available(iOS 14.0, *) {
            completionHandler([.banner, .sound])
        } else {
            completionHandler([.alert, .sound])
        }
    }

    public func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        if let raw = response.notification.request.content.userInfo["url"] as? String,
           let url = URL(string: raw),
           ["http", "https"].contains(url.scheme?.lowercased() ?? "") {
            DispatchQueue.main.async { UIApplication.shared.open(url) }
        }
        completionHandler()
    }

    private func finish(_ result: @escaping FlutterResult, _ value: Any?) {
        DispatchQueue.main.async { result(value) }
    }

    private func text(_ value: Any?, default fallback: String = "") -> String {
        if let value = value as? String { return value }
        if let value = value as? NSNumber { return value.stringValue }
        return fallback
    }

    private func bool(_ value: Any?, default fallback: Bool) -> Bool {
        if let value = value as? Bool { return value }
        if let value = value as? NSNumber { return value.boolValue }
        return fallback
    }

    private func integer(_ value: Any?, default fallback: Int) -> Int {
        if let value = value as? Int { return value }
        if let value = value as? NSNumber { return value.intValue }
        return fallback
    }

    private func intArray(_ value: Any?) -> [Int] {
        (value as? [Any] ?? []).compactMap {
            if let value = $0 as? Int { return value }
            if let value = $0 as? NSNumber { return value.intValue }
            return nil
        }
    }

    private func stringArray(_ value: Any?) -> [String] {
        (value as? [Any] ?? []).compactMap { $0 as? String }
    }
}
