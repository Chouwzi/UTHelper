import CryptoKit
import Flutter
import Security
import UIKit
import UserNotifications

public final class UthBackgroundSyncPlugin: NSObject, FlutterPlugin, UNUserNotificationCenterDelegate {
    private static let channelName = "com.uthelper/background_sync"
    private static let requestPrefix = "uth.deadline."
    private static let immediatePrefix = "uth.immediate."
    private static let classSchedulePrefix = "uth.class."
    private static let settingsKey = "uth.notification.settings"
    private static let receiptsKey = "uth.notification.receipts"
    private static let keychainService = "com.uthelper.app_secrets"
    private static let keychainAccount = "app_secrets_v1"
    private static let keychainLock = NSLock()
    private static let keychainQueue = DispatchQueue(
        label: "com.uthelper.app_secrets.keychain",
        qos: .userInitiated
    )

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

        case "read_app_secrets":
            Self.keychainQueue.async {
                switch self.readKeychainSecrets() {
                case .success(let secrets):
                    self.finish(result, secrets)
                case .failure(let error):
                    self.finish(
                        result,
                        FlutterError(
                            code: "keychain_read_failed",
                            message: error.localizedDescription,
                            details: nil
                        )
                    )
                }
            }

        case "write_app_secrets":
            let arguments = call.arguments as? [String: Any]
            let raw = arguments?["secrets"] as? [String: Any] ?? [:]
            var sanitized: [String: String] = [:]
            for (key, value) in raw {
                let cleanKey = key.trimmingCharacters(in: .whitespacesAndNewlines)
                let cleanValue = text(value)
                if !cleanKey.isEmpty && !cleanValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    sanitized[cleanKey] = cleanValue
                }
            }
            Self.keychainQueue.async {
                let ok = self.writeKeychainSecrets(sanitized)
                self.finish(result, ok)
            }

        case "clear_app_secrets":
            Self.keychainQueue.async {
                let ok = self.clearKeychainSecrets()
                self.finish(result, ok)
            }

        case "import_activities":
            let arguments = call.arguments as? [String: Any]
            let activities = arguments?["activities"] as? [[String: Any]] ?? []
            reconcile(activities: activities, allowImmediate: true, result: result)

        case "schedule_class_sessions":
            let arguments = call.arguments as? [String: Any] ?? [:]
            let sessions = arguments["sessions"] as? [[String: Any]] ?? []
            let enabled = bool(arguments["enabled"], default: true)
            reconcileClassSessions(sessions, enabled: enabled, result: result)

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
                let pending = requests.filter {
                    $0.identifier.hasPrefix(Self.requestPrefix) || $0.identifier.hasPrefix(Self.classSchedulePrefix)
                }.count
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

        case "launch_external_intent":
            let arguments = call.arguments as? [String: Any]
            let rawUri = text(arguments?["uri"]).trimmingCharacters(in: .whitespacesAndNewlines)
            let allowedSchemes: Set<String> = [
                "zalo", "zoomus", "zoomphonecall", "zoom", "msteams",
                "mailto", "tel", "sms", "tg", "fb-messenger", "whatsapp",
                "viber", "skype", "slack", "discord", "googledocs",
                "googlesheets", "googleslides", "googlemeet", "googleclassroom",
                "googledrive", "vnd.youtube", "ms-word", "ms-excel",
                "ms-powerpoint", "ms-onenote", "ms-outlook", "onedrive"
            ]
            guard !rawUri.isEmpty,
                  !rawUri.unicodeScalars.contains(where: { $0.value <= 0x20 || $0.value == 0x7F }),
                  let url = URL(string: rawUri),
                  let scheme = url.scheme?.lowercased(),
                  allowedSchemes.contains(scheme)
            else {
                result(false)
                return
            }
            DispatchQueue.main.async {
                UIApplication.shared.open(url, options: [:]) { ok in
                    self.finish(result, ok)
                }
            }

        case "schedule_periodic":
            result(["scheduled": false, "reason": "ios_foreground_snapshot_only"])

        case "sync_now":
            result("unsupported_on_ios")

        case "cancel_periodic":
            result(nil)

        case "install_update":
            result(["status": "unavailable_on_ios"])

        case "logout":
            // Moodle data only: Portal class-schedule notifications are owned
            // by "schedule_class_sessions" (enabled=false) and must survive a
            // Moodle logout, otherwise a configure() without a token would wipe
            // a freshly armed class schedule.
            clearDeadlineNotifications()
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
            // Countdown alerts are off: drop only the deadline reminders.
            // Class-schedule notifications have their own switch and must
            // survive here (a blanket clear would wipe them too).
            clearDeadlineNotifications()
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
            // iOS has a finite 64-slot pending local-notification budget. Share
            // it with class-schedule requests, keeping the nearest triggers
            // overall instead of relying on silent system eviction.
            var combined: [(Date, UNNotificationRequest, Bool)] = []
            for request in desired.values {
                combined.append((self.triggerDate(of: request), request, true))
            }
            for request in existing where request.identifier.hasPrefix(Self.classSchedulePrefix) {
                combined.append((self.triggerDate(of: request), request, false))
            }
            let kept = self.nearest(combined, limit: 64)
            let selectedRequests = kept.filter { $0.2 }.map { $0.1 }
            let keptIdentifiers = Set(kept.map { $0.1.identifier })
            let droppedClassIdentifiers = combined
                .filter { !$0.2 && !keptIdentifiers.contains($0.1.identifier) }
                .map { $0.1.identifier }
            if !droppedClassIdentifiers.isEmpty {
                self.center.removePendingNotificationRequests(withIdentifiers: droppedClassIdentifiers)
            }

            let oldIdentifiers = Set(
                existing
                    .map(\.identifier)
                    .filter { $0.hasPrefix(Self.requestPrefix) }
            )
            let desiredIdentifiers = Set(selectedRequests.map(\.identifier))
            let removed = oldIdentifiers.subtracting(desiredIdentifiers)
            if !removed.isEmpty {
                self.center.removePendingNotificationRequests(withIdentifiers: Array(removed))
            }

            let group = DispatchGroup()
            let lock = NSLock()
            var failures: [String] = []
            for request in selectedRequests + immediate {
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
                    "scheduled": selectedRequests.count,
                    "deferred": max(0, desired.count - selectedRequests.count),
                    "cancelled": removed.count,
                    "delivered": failures.isEmpty ? immediate.count : 0,
                    "failed": failures.count,
                    "errors": failures,
                ])
            }
        }
    }

    private func reconcileClassSessions(
        _ sessions: [[String: Any]],
        enabled: Bool,
        result: @escaping FlutterResult
    ) {
        center.getPendingNotificationRequests { existing in
            let oldIdentifiers = existing.map(\.identifier).filter {
                $0.hasPrefix(Self.classSchedulePrefix)
            }
            if !oldIdentifiers.isEmpty {
                self.center.removePendingNotificationRequests(withIdentifiers: oldIdentifiers)
            }

            guard enabled else {
                // Pending class requests are all cancelled; delivered banners
                // are dropped as well, including ones whose class has not
                // started yet — a teardown must leave no stale class notice.
                self.removeStaleDeliveredClassNotifications(
                    desiredIdentifiers: [],
                    includeUpcoming: true
                ) {
                    self.finish(result, [
                        "desired": 0,
                        "scheduled": 0,
                        "cancelled": oldIdentifiers.count,
                    ])
                }
                return
            }

            let now = Date().timeIntervalSince1970
            let candidates = sessions.compactMap { session -> (String, Date, UNMutableNotificationContent)? in
                let reminderID = self.text(session["reminder_id"]).trimmingCharacters(in: .whitespacesAndNewlines)
                let subject = self.text(session["subject"]).trimmingCharacters(in: .whitespacesAndNewlines)
                let room = self.text(session["room"]).trimmingCharacters(in: .whitespacesAndNewlines)
                let campus = self.text(session["campus"]).trimmingCharacters(in: .whitespacesAndNewlines)
                let deliveryMode = self.text(session["delivery_mode"]).lowercased()
                let leadHours = self.integer(session["lead_hours"], default: 0)
                let locationIsValid = deliveryMode == "online" ||
                    (deliveryMode == "in_person" && (!room.isEmpty || !campus.isEmpty))
                let scheduledEpoch = (session["scheduled_at_epoch_seconds"] as? NSNumber)?.doubleValue ?? 0
                let startEpoch = (session["start_at_epoch_seconds"] as? NSNumber)?.doubleValue ?? 0
                guard !reminderID.isEmpty, !subject.isEmpty, locationIsValid,
                      (1...3).contains(leadHours), scheduledEpoch > now,
                      startEpoch > scheduledEpoch else { return nil }

                let scheduled = Date(timeIntervalSince1970: scheduledEpoch)
                let start = Date(timeIntervalSince1970: startEpoch)
                let content = UNMutableNotificationContent()
                let modeLabel = deliveryMode == "online" ? "Trực tuyến" : "Trực tiếp"
                content.title = "Sắp đến giờ học (\(modeLabel)): \(subject)"
                var details = ["Còn \(leadHours) giờ nữa", "Bắt đầu lúc \(DateFormatter.localizedString(from: start, dateStyle: .none, timeStyle: .short))"]
                if !room.isEmpty { details.append("Phòng \(room)") }
                if !campus.isEmpty { details.append(campus) }
                let note = self.text(session["note"]).trimmingCharacters(in: .whitespacesAndNewlines)
                if !note.isEmpty { details.append(note) }
                content.body = details.joined(separator: " · ")
                content.sound = .default
                content.userInfo = [
                    "reminder_id": reminderID,
                    "session_id": self.text(session["session_id"]),
                    "start_at": NSNumber(value: startEpoch),
                ]
                return (reminderID, scheduled, content)
            }.sorted { $0.1 < $1.1 }

            // Build the class requests up front so they can compete with
            // deadline requests for the single 64-slot pending budget by
            // nearest trigger, instead of reserving whatever is left over.
            var classRequests: [UNNotificationRequest] = []
            for (reminderID, scheduled, content) in candidates {
                let identifier = Self.classSchedulePrefix + self.stableIdentifier(reminderID)
                let components = Calendar.current.dateComponents(
                    [.year, .month, .day, .hour, .minute, .second],
                    from: scheduled
                )
                classRequests.append(
                    UNNotificationRequest(
                        identifier: identifier,
                        content: content,
                        trigger: UNCalendarNotificationTrigger(dateMatching: components, repeats: false)
                    )
                )
            }

            var combined: [(Date, UNNotificationRequest, Bool)] = []
            for request in classRequests {
                combined.append((self.triggerDate(of: request), request, true))
            }
            for request in existing where request.identifier.hasPrefix(Self.requestPrefix) {
                combined.append((self.triggerDate(of: request), request, false))
            }
            let kept = self.nearest(combined, limit: 64)
            let selectedRequests = kept.filter { $0.2 }.map { $0.1 }
            let keptIdentifiers = Set(kept.map { $0.1.identifier })
            let droppedDeadlineIdentifiers = combined
                .filter { !$0.2 && !keptIdentifiers.contains($0.1.identifier) }
                .map { $0.1.identifier }
            if !droppedDeadlineIdentifiers.isEmpty {
                self.center.removePendingNotificationRequests(withIdentifiers: droppedDeadlineIdentifiers)
            }

            let group = DispatchGroup()
            let lock = NSLock()
            var failures: [String] = []
            for request in selectedRequests {
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
                let desiredIdentifiers = Set(classRequests.map(\.identifier))
                self.removeStaleDeliveredClassNotifications(desiredIdentifiers: desiredIdentifiers) {
                    self.finish(result, [
                        "desired": candidates.count,
                        "scheduled": selectedRequests.count - failures.count,
                        "cancelled": oldIdentifiers.count,
                        "failed": failures.count,
                        "errors": failures,
                    ])
                }
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

    private func triggerDate(of request: UNNotificationRequest) -> Date {
        (request.trigger as? UNCalendarNotificationTrigger)?.nextTriggerDate() ?? .distantFuture
    }

    /// Picks the nearest `limit` entries by trigger date, breaking ties on the
    /// notification identifier so the shared 64-slot budget is deterministic.
    private func nearest(
        _ entries: [(Date, UNNotificationRequest, Bool)],
        limit: Int
    ) -> [(Date, UNNotificationRequest, Bool)] {
        let sorted = entries.sorted { left, right in
            if left.0 != right.0 { return left.0 < right.0 }
            return left.1.identifier < right.1.identifier
        }
        return Array(sorted.prefix(limit))
    }

    /// Removes delivered class-schedule notifications that are no longer desired.
    /// A missing/zero `start_at` counts as started; `includeUpcoming: true`
    /// (used by full teardowns) also drops banners for classes still ahead.
    /// Pending identifiers can never remove delivered notifications.
    private func removeStaleDeliveredClassNotifications(
        desiredIdentifiers: Set<String>,
        includeUpcoming: Bool = false,
        completion: @escaping () -> Void
    ) {
        center.getDeliveredNotifications { delivered in
            let now = Date().timeIntervalSince1970
            let stale = delivered.compactMap { item -> String? in
                let identifier = item.request.identifier
                guard identifier.hasPrefix(Self.classSchedulePrefix),
                      !desiredIdentifiers.contains(identifier) else { return nil }
                let start = (item.request.content.userInfo["start_at"] as? NSNumber)?.doubleValue ?? 0
                return (includeUpcoming || start <= now) ? identifier : nil
            }
            if !stale.isEmpty {
                self.center.removeDeliveredNotifications(withIdentifiers: stale)
            }
            completion()
        }
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

    /// Clears only countdown/deadline notifications, preserving class-schedule
    /// reminders. Used when the deadline switch is turned off and on Moodle
    /// logout, where the Portal schedule must stay armed.
    private func clearDeadlineNotifications() {
        center.getPendingNotificationRequests { requests in
            let pendingIds = requests.map(\.identifier).filter {
                $0.hasPrefix(Self.requestPrefix) || $0.hasPrefix(Self.immediatePrefix)
            }
            if !pendingIds.isEmpty {
                self.center.removePendingNotificationRequests(withIdentifiers: pendingIds)
            }
            self.center.getDeliveredNotifications { delivered in
                let deliveredIds = delivered.map(\.request.identifier).filter {
                    $0.hasPrefix(Self.requestPrefix) || $0.hasPrefix(Self.immediatePrefix)
                }
                if !deliveredIds.isEmpty {
                    self.center.removeDeliveredNotifications(withIdentifiers: deliveredIds)
                }
            }
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

    private func baseKeychainQuery() -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.keychainService,
            kSecAttrAccount as String: Self.keychainAccount,
        ]
    }

    private func readKeychainSecrets() -> Result<[String: String], NSError> {
        Self.keychainLock.lock()
        defer { Self.keychainLock.unlock() }

        var query = baseKeychainQuery()
        query[kSecReturnData as String] = kCFBooleanTrue
        query[kSecMatchLimit as String] = kSecMatchLimitOne

        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound {
            return .success([:])
        }
        guard status == errSecSuccess else {
            return .failure(
                NSError(
                    domain: NSOSStatusErrorDomain,
                    code: Int(status),
                    userInfo: [NSLocalizedDescriptionKey: "Keychain read failed (OSStatus \(status))"]
                )
            )
        }
        guard let data = item as? Data,
              let decoded = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            // Item exists and Keychain unlocked (errSecSuccess), but payload is corrupt/unreadable.
            // Self-heal by deleting the corrupt entry directly while holding keychainLock so the
            // user is never permanently locked out across app reinstalls.
            SecItemDelete(baseKeychainQuery() as CFDictionary)
            return .success([:])
        }
        var secrets: [String: String] = [:]
        for (key, value) in decoded {
            let cleanKey = key.trimmingCharacters(in: .whitespacesAndNewlines)
            if let strValue = value as? String,
               !cleanKey.isEmpty,
               !strValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                secrets[cleanKey] = strValue
            }
        }
        return .success(secrets)
    }

    private func writeKeychainSecrets(_ secrets: [String: String]) -> Bool {
        if secrets.isEmpty {
            return clearKeychainSecrets()
        }
        guard let payload = try? JSONSerialization.data(withJSONObject: secrets, options: []) else {
            return false
        }

        Self.keychainLock.lock()
        defer { Self.keychainLock.unlock() }

        let query = baseKeychainQuery()
        let updateAttributes: [String: Any] = [
            kSecValueData as String: payload,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        let updateStatus = SecItemUpdate(query as CFDictionary, updateAttributes as CFDictionary)
        if updateStatus == errSecSuccess {
            return true
        }
        if updateStatus != errSecItemNotFound {
            return false
        }

        var addQuery = query
        addQuery[kSecValueData as String] = payload
        addQuery[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let addStatus = SecItemAdd(addQuery as CFDictionary, nil)
        return addStatus == errSecSuccess
    }

    private func clearKeychainSecrets() -> Bool {
        Self.keychainLock.lock()
        defer { Self.keychainLock.unlock() }

        let status = SecItemDelete(baseKeychainQuery() as CFDictionary)
        return status == errSecSuccess || status == errSecItemNotFound
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
