import SwiftUI
import TickrDesign
import TickrKit

/// The Settings window (⌘,) as the design draws it: the pane's name in small monospace in the
/// title bar, the design's tab strip, then the pane's cards on the pale background. The app's own
/// preferences are editable; the bot's configuration is shown read-only because it lives in
/// config/bot.toml, not in the app.
struct SettingsView: View {
  @Environment(AppModel.self) private var model
  @State private var tab: SettingsTab = .general
  @State private var contentHeight: CGFloat = 0

  /// Width of the Settings window, in points (the design's 820).
  static let width: CGFloat = 820
  /// Tallest the pane area grows to its content before it scrolls, in points.
  static let maxPaneHeight: CGFloat = 720

  var body: some View {
    VStack(spacing: 0) {
      SettingsTabBar(selection: $tab)
      ScrollView {
        pane
          .padding(.horizontal, Tokens.Space.xl)
          .padding(.vertical, Tokens.Space.l)
          .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { contentHeight = $0 }
      }
      .scrollBounceBehavior(.basedOnSize)
      .frame(height: min(max(contentHeight, 1), Self.maxPaneHeight))
    }
    .frame(width: Self.width)
    .background(Palette.background)
    // The window keeps the pane's name (Window menu); the title bar shows it in the design's
    // small monospace instead of the system's bold title.
    .navigationTitle(Text(tab.title))
    .toolbar(removing: .title)
    .toolbar {
      ToolbarItem(placement: .principal) {
        Text(tab.title)
          .font(Tokens.Typography.calloutMono)
          .foregroundStyle(Palette.textQuaternary)
      }
    }
    .toolbarBackground(Palette.titleBar, for: .windowToolbar)
    .toolbarBackgroundVisibility(.visible, for: .windowToolbar)
    .task(id: model.baseURL) { await model.followJobs() }
  }

  @ViewBuilder private var pane: some View {
    VStack(alignment: .leading, spacing: Tokens.Space.m) {
      switch tab {
      case .general: GeneralSettings()
      case .trading: TradingSettings()
      case .notifications: NotificationSettings()
      case .environment: EnvironmentSettings()
      case .storage: StorageSettings()
      case .advanced: AdvancedSettings()
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
  }
}

/// Where a read-only value comes from, under each bot-configuration card.
private var configFootnote: Text { Text(L10n.Settings.configFootnote) }

private struct GeneralSettings: View {
  @Environment(AppModel.self) private var model
  @AppStorage(Defaults.apiBaseURL) private var apiBaseURL = APIClient.defaultBaseURL.absoluteString
  @AppStorage(Defaults.showMenuBarExtra) private var showMenuBarExtra = true
  @State private var draft = ""
  @State private var invalid = false

  /// URL schemes the local API address may use.
  static let schemes: Set = ["http", "https"]

  var body: some View {
    Group {
      SettingsCard(L10n.Settings.botConnection, footnote: connectionNote,
        footnoteColor: model.fetchError == nil ? Palette.textQuaternary : Palette.lossText) {
        HStack(spacing: Tokens.Space.s) {
          TextField(String(localized: L10n.Settings.apiAddress), text: $draft)
            .textFieldStyle(.roundedBorder)
            .font(Tokens.Typography.calloutMono)
            .onSubmit(apply)
          Button(String(localized: L10n.Settings.useDefault)) {
            draft = APIClient.defaultBaseURL.absoluteString
            apply()
          }
            .buttonStyle(.action(.secondary, size: .compact))
          Button(String(localized: L10n.Settings.connect), action: apply)
            .buttonStyle(.action(.primary, size: .compact))
            .keyboardShortcut(.defaultAction)
            .disabled(draft == model.baseURL.absoluteString)
        }
      }
      JobsSection()
      SettingsCard(L10n.Settings.menuBar) {
        Toggle(isOn: $showMenuBarExtra) {
          Text(L10n.Settings.showInMenuBar)
            .font(Tokens.Typography.callout)
            .foregroundStyle(Palette.textSecondary)
        }
        .toggleStyle(.switch)
        .controlSize(.small)
        .tint(Palette.accent)
      }
      SettingsCard(L10n.Settings.appearance) {
        SettingsRow(L10n.Settings.theme) { Text(L10n.Settings.followsSystem) }
      }
    }
    .onAppear { draft = model.baseURL.absoluteString }
    .alert(String(localized: L10n.Settings.invalidAddress), isPresented: $invalid) {
      Button(String(localized: L10n.Settings.ok), role: .cancel) {}
    } message: {
      Text(L10n.Settings.enterAddress(APIClient.defaultBaseURL.absoluteString))
    }
  }

  private var connectionNote: Text {
    if let error = model.fetchError { return Text(L10n.Settings.notReachable(error)) }
    return Text(L10n.Settings.connectedTo(model.baseURL.absoluteString))
  }

  private func apply() {
    guard let url = URL(string: draft.trimmingCharacters(in: .whitespaces)),
      Self.schemes.contains(url.scheme ?? ""), url.host() != nil else {
      invalid = true
      return
    }
    apiBaseURL = url.absoluteString
    model.connect(to: url)
  }
}

private struct TradingSettings: View {
  @Environment(AppModel.self) private var model

  var body: some View {
    if let settings = model.settings {
      SettingsCard(L10n.Common.strategy, footnote: configFootnote) {
        SettingsRow(L10n.Settings.activeStrategy, value: settings.activeStrategy)
      }
      SettingsCard(L10n.Common.risk, footnote: configFootnote) {
        SettingsRow(L10n.Settings.plannedLoss, value: Format.usd(settings.risk.plannedLossUsd.value))
        SettingsRow(L10n.Settings.notionalCap, value: Format.usd(settings.risk.positionNotionalCapUsd.value))
        SettingsRow(L10n.Settings.dailyLossLimit, value: Format.usd(settings.risk.maxDailyLossUsd.value))
        SettingsRow(L10n.Settings.maxOpenPositions, value: "\(settings.risk.maxOpenPositions)")
        SettingsRow(L10n.Settings.maxQuoteAge, value: Format.seconds(settings.risk.maxQuoteAgeSeconds.value))
      }
      SettingsCard(L10n.Settings.session, footnote: configFootnote) {
        SettingsRow(L10n.Settings.marketTimezone, value: settings.session.timezone)
        SettingsRow(L10n.Settings.lastEntry, value: Self.beforeClose(settings.session.lastEntryMinutesBeforeClose))
        SettingsRow(L10n.Settings.flatten, value: Self.beforeClose(settings.session.flattenMinutesBeforeClose))
      }
      FeaturesCard(settings: settings)
    } else {
      SettingsCard(L10n.Common.strategy) {
        Group {
          if let error = model.settingsError { Text(verbatim: error) } else { Text(L10n.Settings.readingSettings) }
        }
        .font(Tokens.Typography.callout)
        .foregroundStyle(Palette.textTertiary)
      }
    }
  }

  private static func beforeClose(_ minutes: Int) -> String { String(localized: L10n.Settings.beforeClose(minutes)) }
}

/// The design's "Features · from config": switches that show what the configuration turns on.
/// Half days and live observers are real values; the rest are planned and marked so.
private struct FeaturesCard: View {
  let settings: SettingsV1

  var body: some View {
    SettingsCard(L10n.Settings.features, footnote: Text(L10n.Settings.featuresFootnote)) {
      row(L10n.Settings.featureTrailing, on: false) { PlannedTag() }
      row(L10n.Settings.featureExtendedHours, on: false) { PlannedTag() }
      row(L10n.Settings.featureShort, on: false) { PlannedTag() }
      row(L10n.Settings.featureChase, on: false) { PlannedTag() }
      row(L10n.Settings.halfDays, on: settings.session.halfDaysEnabled) {
        Text(settings.session.halfDaysEnabled ? L10n.Settings.traded : L10n.Settings.notTraded)
      }
      row(L10n.Settings.liveObservers, on: !settings.observers.isEmpty) {
        Text(verbatim: settings.observers.isEmpty
          ? String(localized: L10n.Common.none) : settings.observers.formatted(.list(type: .and)))
      }
    }
  }

  private func row(_ title: LocalizedStringResource, on: Bool, @ViewBuilder tag: () -> some View) -> some View {
    HStack(spacing: Tokens.Space.m) {
      ConfigSwitch(on: on)
      Text(title)
        .font(Tokens.Typography.callout)
        .foregroundStyle(Palette.textSecondary)
      Spacer(minLength: Tokens.Space.s)
      tag()
        .font(Tokens.Typography.annotation)
        .foregroundStyle(Palette.textQuaternary)
    }
    .accessibilityElement(children: .combine)
  }
}

/// The alerts the bot sends (runtime/alerts.py `AlertKind`), one row each as the design lists
/// them, with a dot of their weight; every one is on and goes out as a Mac notification and, with
/// a recipient in the bot's env file, as an iMessage. Nothing here is a switch, because the bot
/// decides; phone push waits for the iOS app.
private struct NotificationSettings: View {
  /// One row: what happened and how loud it is.
  struct AlertRow: Identifiable {
    let tone: Tone
    let title: LocalizedStringResource
    var id: String { title.key }
  }

  /// The rows, loudest first: the design's eleven, then the watchdog, the morning checks and the
  /// observers, which the bot sends too.
  static var rows: [AlertRow] {
    [
      AlertRow(tone: .loss, title: L10n.Settings.alertUnprotected),
      AlertRow(tone: .loss, title: L10n.Settings.alertStopRejected),
      AlertRow(tone: .loss, title: L10n.Settings.alertExitRefused),
      AlertRow(tone: .loss, title: L10n.Settings.alertUnknownStatus),
      AlertRow(tone: .warning, title: L10n.Settings.alertForeign),
      AlertRow(tone: .warning, title: L10n.Settings.alertBrokerReads),
      AlertRow(tone: .warning, title: L10n.Settings.alertStreamLost),
      AlertRow(tone: .warning, title: L10n.Settings.alertLossLimit),
      AlertRow(tone: .info, title: L10n.Settings.alertBot),
      AlertRow(tone: .info, title: L10n.Settings.alertToken),
      AlertRow(tone: .info, title: L10n.Settings.alertNotFlat),
      AlertRow(tone: .info, title: L10n.Settings.alertWatchdog),
      AlertRow(tone: .info, title: L10n.Settings.alertMorning),
      AlertRow(tone: .info, title: L10n.Settings.alertObservers),
    ]
  }

  var body: some View {
    SettingsCard(L10n.Settings.alerts, footnote: Text(L10n.Settings.alertsLegend)) {
      ForEach(Self.rows) { row in
        HStack(spacing: Tokens.Space.s) {
          PulseDot(row.tone.fill, size: Tokens.Size.dotSmall, pulsing: false)
          Text(row.title)
            .font(Tokens.Typography.callout)
            .foregroundStyle(Palette.textSecondary)
            .lineLimit(1)
          Spacer(minLength: Tokens.Space.s)
          Circle().fill(Palette.profit)
            .frame(width: Tokens.Size.dotSmall, height: Tokens.Size.dotSmall)
            .frame(width: Tokens.Size.rowSymbol)
          Circle().strokeBorder(Palette.highlight, lineWidth: Tokens.Stroke.thin)
            .frame(width: Tokens.Size.dotSmall, height: Tokens.Size.dotSmall)
            .frame(width: Tokens.Size.rowSymbol)
        }
        .accessibilityElement(children: .combine)
      }
    } trailing: {
      Text(L10n.Settings.alertColumns)
        .font(Tokens.Typography.annotation)
        .foregroundStyle(Palette.textQuaternary)
    }
    SettingsCard(L10n.Settings.botAlerts) {
      Text(L10n.Settings.alertsFooter)
        .font(Tokens.Typography.callout)
        .foregroundStyle(Palette.textTertiary)
        .fixedSize(horizontal: false, vertical: true)
    }
  }
}

/// The environment and the trading gate as the design's two-part strip: the environment the bot
/// runs in lit in its tone with the gate's state, the others muted and closed; then the cause.
private struct EnvironmentSettings: View {
  @Environment(AppModel.self) private var model

  var body: some View {
    let raw = model.status?.environment ?? model.settings?.environment
    SettingsCard(L10n.Settings.environmentGate, footnote: Text(L10n.Settings.environmentFixed)) {
      HStack(spacing: Tokens.Space.xs) {
        ForEach(Self.segments(current: raw), id: \.self) { segment in
          let active = segment == raw
          Text(verbatim: label(segment, active: active))
            .font(Tokens.Typography.annotationBold)
            .lineLimit(1)
            .frame(maxWidth: .infinity)
            .padding(.vertical, Tokens.Space.s)
            .foregroundStyle(active ? tone(segment).text : Palette.textQuaternary)
            .background(active ? tone(segment).fill.opacity(Tokens.Opacity.glow) : .clear,
              in: RoundedRectangle(cornerRadius: Tokens.Radius.small, style: .continuous))
        }
      }
      .padding(Tokens.Space.xs)
      .background(Palette.surfaceSunken, in: RoundedRectangle(cornerRadius: Tokens.Radius.medium, style: .continuous))
      .overlay(RoundedRectangle(cornerRadius: Tokens.Radius.medium, style: .continuous)
        .strokeBorder(Palette.separator, lineWidth: Tokens.Stroke.thin))
      if let gate = model.status?.tradingGate {
        SettingsRow(L10n.Settings.gateCause, value: ApiText.gate(gate))
      }
      SettingsRow(L10n.Settings.apiVersion, value: model.settings.map { "\($0.apiVersion)" } ?? Format.missing)
    }
  }

  /// The parts of the strip: Sandbox and Production, with Demo in front while the demo runs, and
  /// an environment this app does not know in front of them.
  static func segments(current: String?) -> [String] {
    let known = [BotEnvironment.sandbox.rawValue, BotEnvironment.production.rawValue]
    guard let current, !known.contains(current) else { return known }
    return [current] + known
  }

  /// "SANDBOX · gate open" for the environment in use, "PRODUCTION · closed" for the others.
  private func label(_ raw: String, active: Bool) -> String {
    let name = ApiText.environment(raw).localizedUppercase
    guard active else { return name + Format.itemSeparator + String(localized: L10n.Settings.gateClosed) }
    let open = model.status?.tradingGate.open
    let state = open.map { String(localized: $0 ? L10n.Settings.gateOpenShort : L10n.Settings.gateClosedShort) }
    return state.map { name + Format.itemSeparator + $0 } ?? name
  }

  private func tone(_ raw: String) -> Tone {
    raw == BotEnvironment.production.rawValue ? .loss : .warning
  }
}

private struct StorageSettings: View {
  @Environment(AppModel.self) private var model

  var body: some View {
    BackupSection()
    SettingsCard(L10n.Settings.history, footnote: historyNote,
      footnoteColor: model.historyError == nil ? Palette.textQuaternary : Palette.lossText) {
      SettingsRow(L10n.Settings.recordedDays, value: "\(model.days.count)")
      SettingsRow(L10n.Settings.firstRecordedDay, value: model.days.last.map { Format.dayLabel($0.day) } ?? Format.missing)
      SettingsRow(L10n.Settings.daysWithBars, value: "\(model.days.filter(\.hasBars).count)")
    }
  }

  private var historyNote: Text {
    model.historyError.map { Text(verbatim: $0) } ?? Text(L10n.Settings.historySource)
  }
}

private struct AdvancedSettings: View {
  @Environment(AppModel.self) private var model

  var body: some View {
    SettingsCard(L10n.Common.bot, footnote: configFootnote) {
      SettingsRow(L10n.Settings.statusRefresh,
        value: model.settings.map { String(localized: L10n.Settings.every(Format.seconds($0.pollSeconds.value))) }
          ?? Format.missing)
      SettingsRow(L10n.Settings.staleAfter, value: model.settings.map { Format.seconds($0.statusStaleSeconds.value) }
        ?? Format.missing)
    }
    SettingsCard(L10n.Settings.notAvailableYet) {
      SettingsRow(L10n.Settings.startStop) { PlannedTag(.needsAPI) }
      SettingsRow(L10n.Settings.editConfig) { PlannedTag() }
    }
  }
}

/// What runs on its own on the Mac (GET /jobs) as the design's "launchd jobs" list: a small dot
/// in the job's colour (pulsing while the bot runs), the job's name in monospace, its state, and
/// its times at the right. Read while Settings is open.
struct JobsSection: View {
  @Environment(AppModel.self) private var model

  /// Width of the name column, in points (the design's 76, widened for the Russian names).
  static let nameWidth: CGFloat = 150
  /// Days in a week; launchd numbers them 0 (or 7) Sunday, 1 Monday … 6 Saturday.
  static let week = 7
  /// Shortest run of days written as a range ("Mon–Fri") rather than a list.
  static let shortestRange = 3
  /// Joins the first and last day of a range.
  static let rangeDash = "–"

  var body: some View {
    SettingsCard(L10n.Settings.jobs, footnote: Text(L10n.Settings.jobsFooter)) {
      if let jobs = model.jobs {
        service(jobs)
        reconcile(jobs.reconcile)
        ForEach(jobs.launchd, id: \.name) { job in
          row(tone: .info, live: false, name: Text(verbatim: Self.title(job.name)), state: Text(verbatim: "")) {
            Text(verbatim: job.localTime + Format.itemSeparator + Self.days(job.weekdays))
          }
        }
      } else if let error = model.jobsError {
        Text(verbatim: error).font(Tokens.Typography.callout).foregroundStyle(Palette.warningText)
      } else {
        ProgressView().controlSize(.small)
      }
    }
  }

  /// One job: dot, name, state, times.
  private func row(tone: Tone, live: Bool, name: Text, state: Text, @ViewBuilder meta: () -> some View) -> some View {
    HStack(spacing: Tokens.Space.s) {
      PulseDot(tone.fill, pulsing: live, event: live ? model.jobs.map { AnyHashable($0.servedAt) } : nil)
      name
        .font(Tokens.Typography.calloutMono)
        .foregroundStyle(Palette.textPrimary)
        .lineLimit(1)
        .frame(width: Self.nameWidth, alignment: .leading)
      state
        .font(Tokens.Typography.callout)
        .foregroundStyle(Palette.textTertiary)
        .lineLimit(1)
      Spacer(minLength: Tokens.Space.s)
      meta()
        .font(Tokens.Typography.annotation)
        .foregroundStyle(Palette.textQuaternary)
    }
    .accessibilityElement(children: .combine)
  }

  /// The bot's state as its status reports it; a stale status proves nothing about the bot now, so
  /// it says so instead of repeating the last state.
  private func service(_ jobs: JobsV1) -> some View {
    let running = jobs.botState == .running && !jobs.statusStale
    let tone: Tone = jobs.statusStale ? .warning : running ? .accent : .neutral
    let title = jobs.statusStale ? String(localized: L10n.Settings.notReported)
      : jobs.botState?.title ?? String(localized: L10n.Settings.notRunning)
    return row(tone: tone, live: running, name: Text(L10n.Settings.botService), state: Text(verbatim: title)) {
      if running, let started = jobs.botStartedAt {
        Text(L10n.Settings.since(Format.marketMinute(started.date)))
      }
    }
  }

  /// A launchd job's name in the person's language; a job this app does not know reads as
  /// "Other job", never as its launchd name.
  static func title(_ name: String) -> String {
    guard let job = LaunchdJob(rawValue: name) else { return String(localized: L10n.Settings.jobOther) }
    switch job {
    case .preflight: return String(localized: L10n.Settings.jobPreflight)
    case .bot: return String(localized: L10n.Settings.jobBot)
    case .watchdog: return String(localized: L10n.Settings.jobWatchdog)
    }
  }

  /// The launchd jobs the bot installs on the Mac, by their wire names in jobs-v1.
  enum LaunchdJob: String {
    case preflight, bot, watchdog
  }

  /// The next reconcile counting down while the bot runs, else when it last ran (New York time).
  private func reconcile(_ job: ReconcileJobV1) -> some View {
    row(tone: .info, live: false, name: Text(L10n.Settings.reconcile), state: Text(verbatim: "")) {
      if let next = job.nextAt?.date, next > model.botNow {
        HStack(spacing: Tokens.Space.xs) {
          Text(L10n.Settings.nextIn)
          Text(timerInterval: Date.now...Date.now.addingTimeInterval(next.timeIntervalSince(model.botNow)))
            .monospacedDigit()
        }
      } else if let last = job.lastAt {
        Text(L10n.Settings.lastAt(Format.marketTime(last.date)))
      } else {
        Text(verbatim: Format.missing)
      }
    }
  }

  /// "Mon–Fri" for a run of days, else "Mon, Wed"; in the person's language.
  static func days(_ weekdays: [Int]) -> String {
    let symbols = Calendar.current.shortWeekdaySymbols
    // Monday first, Sunday last, as a working week reads.
    let order = Array(Set(weekdays.map { $0 % week })).sorted { ($0 + week - 1) % week < ($1 + week - 1) % week }
    guard order.allSatisfy({ symbols.indices.contains($0) }), let first = order.first, let last = order.last else {
      return weekdays.map(String.init).joined(separator: Format.listSeparator)
    }
    let contiguous = zip(order, order.dropFirst()).allSatisfy { ($0.0 + 1) % week == $0.1 }
    if contiguous && order.count >= shortestRange {
      return symbols[first] + rangeDash + symbols[last]
    }
    return order.map { symbols[$0] }.joined(separator: Format.listSeparator)
  }
}

/// The bot's database backup (GET /jobs) as the design's "Backups · storage": a green check with
/// what the backup is and when the last one was made, or the failure when it is newer, with its
/// error; why neither is known when the bot cannot tell; and how clean-up runs.
private struct BackupSection: View {
  @Environment(AppModel.self) private var model

  /// Symbols of the states and of the clean-up note.
  static let verifiedSymbol = "checkmark.circle.fill"
  static let failedSymbol = "xmark.circle.fill"
  static let unknownSymbol = "circle.dashed"
  static let cleanupSymbol = "trash"

  var body: some View {
    SettingsCard(L10n.Settings.backups, footnote: Text(L10n.Settings.backupFooter)) {
      if let job = model.jobs?.backup {
        let failed = job.lastFailedAt.map { failure in job.lastAt.map { failure.date > $0.date } ?? true } ?? false
        if failed, let at = job.lastFailedAt {
          state(Self.failedSymbol, tone: .loss, Text(L10n.Settings.backupFailed(Self.when(at.date))))
          if let error = job.lastFailedError {
            Text(verbatim: error).font(Tokens.Typography.callout).foregroundStyle(Palette.lossText)
          }
        } else if let at = job.lastAt {
          state(Self.verifiedSymbol, tone: .profit, Text(L10n.Settings.backupVerified))
          Text(L10n.Settings.backupLast(Self.when(at.date)))
            .font(Tokens.Typography.annotation)
            .foregroundStyle(Palette.textQuaternary)
        } else {
          state(Self.unknownSymbol, tone: .neutral, Text(verbatim: Format.missing))
        }
        if let cause = job.cause {
          Text(verbatim: cause).font(Tokens.Typography.callout).foregroundStyle(Palette.warningText)
        }
      } else if let error = model.jobsError {
        Text(verbatim: error).font(Tokens.Typography.callout).foregroundStyle(Palette.warningText)
      } else {
        ProgressView().controlSize(.small)
      }
      Label {
        Text(L10n.Settings.cleanup)
      } icon: {
        Image(systemName: Self.cleanupSymbol)
      }
      .labelStyle(ControlLabelStyle())
      .font(Tokens.Typography.callout)
      .foregroundStyle(Palette.textTertiary)
      .fixedSize(horizontal: false, vertical: true)
      .padding(.top, Tokens.Space.xs)
    }
  }

  private func state(_ symbol: String, tone: Tone, _ text: Text) -> some View {
    HStack(spacing: Tokens.Space.s) {
      Image(systemName: symbol)
        .font(Tokens.Typography.symbol)
        .foregroundStyle(tone.fill)
      text
        .font(Tokens.Typography.callout)
        .foregroundStyle(Palette.textPrimary)
    }
  }

  /// "Fri Nov 13 16:20" in New York time.
  private static func when(_ date: Date) -> String {
    Format.dayLabel(WireDate(newYorkDateOf: date)) + " " + Format.marketMinute(date)
  }
}
