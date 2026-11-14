import ApertureKit
import SwiftUI

/// Five tabs, ordered by use during a trading day. Settings live behind the gear on Home.
struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        TabView(selection: $model.selectedTab) {
            NavigationStack { HomeView() }
                .tabItem { Label("Home", systemImage: "square.grid.2x2") }
                .tag(AppTab.home)
            NavigationStack { PositionsView() }
                .tabItem { Label("Positions", systemImage: "lock.shield") }
                .tag(AppTab.positions)
            NavigationStack { ChartTab() }
                .tabItem { Label("Chart", systemImage: "chart.xyaxis.line") }
                .tag(AppTab.chart)
            NavigationStack { ActivityView() }
                .tabItem { Label("Activity", systemImage: "list.bullet.rectangle") }
                .tag(AppTab.activity)
            NavigationStack { LabView() }
                .tabItem { Label("Lab", systemImage: "sparkles") }
                .tag(AppTab.lab)
        }
        .modifier(NoticeOverlay())
        .onOpenURL { url in
            if let pairing = Pairing.parse(url.absoluteString) {
                model.pendingPairing = pairing
            } else {
                model.notice = Notice(text: "That link is not an Aperture pairing link.", tone: .failure)
            }
        }
        .alert(
            "Pair with \(model.pendingPairing?.url.host ?? "this Mac")?",
            isPresented: Binding(get: { model.pendingPairing != nil }, set: { if !$0 { model.pendingPairing = nil } }),
            presenting: model.pendingPairing
        ) { pairing in
            Button("Pair") { model.pair(pairing) }
            Button("Cancel", role: .cancel) {}
        } message: { _ in
            Text("The phone will read the bot's status from this address and can send commands. "
                + "Any earlier pairing is replaced.")
        }
    }
}
