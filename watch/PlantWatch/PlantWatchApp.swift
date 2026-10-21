import SwiftUI

private enum WatchStyle {
    // Same palette as frontend/src/styles.css.
    static let accent = Color(red: 118 / 255, green: 226 / 255, blue: 56 / 255)
    static let surface = Color(white: 22 / 255)
}

@main
struct PlantWatchApp: App {
    var body: some Scene {
        WindowGroup {
            PlantView()
        }
    }
}

private enum DemoScenario: String, CaseIterable, Identifiable {
    case healthy = "Всё хорошо"
    case dry = "Сухая почва"
    case watered = "После полива"

    var id: Self { self }

    var moisture: Int {
        switch self {
        case .healthy: return 46
        case .dry: return 26
        case .watered: return 58
        }
    }

    var hoursUntilWatering: Int { max(0, (moisture - 30) / 2) }

    var color: Color { self == .dry ? .orange : WatchStyle.accent }

    var message: String {
        switch self {
        case .healthy: return "Всё хорошо! Воды пока хватает."
        case .dry: return "Пить хочу! Почва подсохла."
        case .watered: return "Спасибо! Теперь воды хватает."
        }
    }
}

private struct PlantView: View {
    @StateObject private var store = WatchStore()
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.isLuminanceReduced) private var isLuminanceReduced
    @State private var now = Date()
    @State private var confirmingForget = false
    @State private var scenario = DemoScenario.healthy
    @State private var confirmingWater = false
    @State private var page = 0
    @State private var lastWateredAt = Date().addingTimeInterval(-2 * 3600)
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        TabView(selection: $page) {
            ScrollView {
                VStack(spacing: 8) {
                    header
                    if !store.isDemo { connectionStatus }
                    plantCard
                    LazyVGrid(columns: metricColumns, spacing: 8) {
                        metric("Темп. дома", value: temperatureText, icon: "thermometer.medium")
                        metric("Влажн. воздуха", value: humidityText, icon: "humidity.fill")
                    }
                    lastWaterCard
                    Text(assistantMessage)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 4)
                    VStack(alignment: .leading, spacing: 4) {
                        Label(store.isDemo ? "Пример прогноза" : "Прогноз сервера", systemImage: "clock")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        Text(forecastText)
                            .font(.footnote.weight(.semibold))
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(10)
                    .background(WatchStyle.surface, in: RoundedRectangle(cornerRadius: 16))
                    if !store.isDemo {
                        Button("Обновить") { store.showLive() }
                            .disabled(store.isLoading || store.settings == nil)
                    }
                    Text(store.isDemo ? "Вымышленные показания" : (store.settings?.device ?? "Настрой подключение →"))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                .padding(.horizontal, 8)
            }
            .tag(0)

            connectionSettings
                .tag(1)

            if store.isDemo {
                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        header
                        Text("Попробуй полив")
                            .font(.headline)
                        Text("Кнопка меняет только пример на часах. Насос не включается.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                        Button {
                            confirmingWater = true
                        } label: {
                            Label("Демо-полив", systemImage: "drop.fill")
                                .frame(maxWidth: .infinity)
                        }
                        .tint(WatchStyle.accent)
                        .disabled(scenario == .watered)
    
                        if scenario == .watered {
                            Label("В примере: 3 с, почва 58%", systemImage: "checkmark.circle.fill")
                                .font(.footnote)
                                .foregroundStyle(WatchStyle.accent)
                        }
    
                        Picker("Сценарий", selection: Binding(get: { scenario }, set: selectScenario)) {
                            ForEach(DemoScenario.allCases) { item in
                                Text(item.rawValue).tag(item)
                            }
                        }
                        Button("Начать заново") {
                            selectScenario(.healthy)
                        }
                        .font(.footnote)
    
                        Text("Прогноз — пример: высыхание на 2%/ч до порога 30%. Это не результат ИИ.")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.horizontal, 8)
                }
                .tag(2)
            }
        }
        .tabViewStyle(.page)
        .onAppear {
            store.setActive(scenePhase == .active && !isLuminanceReduced)
            if store.settings == nil && !store.isDemo { page = 1 }
        }
        .onChange(of: scenePhase) { _, phase in store.setActive(phase == .active && !isLuminanceReduced) }
        .onChange(of: isLuminanceReduced) { _, reduced in store.setActive(scenePhase == .active && !reduced) }
        .task(id: store.revision) { await store.poll() }
        .task(id: scenePhase == .active && !isLuminanceReduced) {
            while !Task.isCancelled && scenePhase == .active && !isLuminanceReduced {
                now = Date()
                do { try await Task.sleep(nanoseconds: 10_000_000_000) }
                catch { return }
            }
        }
        .alert("Забыть подключение?", isPresented: $confirmingForget) {
            Button("Удалить", role: .destructive) { store.forget() }
            Button("Отмена", role: .cancel) {}
        } message: { Text("Адрес и пароль будут удалены из Keychain часов.") }
        .alert("Сымитировать полив?", isPresented: $confirmingWater) {
            Button("Полить в демо") {
                withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.25)) {
                    selectScenario(.watered)
                }
            }
            Button("Отмена", role: .cancel) {}
        } message: {
            Text("Только демонстрация на часах, без команды устройству.")
        }
    }

    private var header: some View {
        HStack {
            Label("Мой цветок", systemImage: "leaf.fill")
                .font(.caption.weight(.semibold))
                .foregroundStyle(WatchStyle.accent)
            Spacer(minLength: 4)
            Text(store.isDemo || store.snapshot?.state.demo == true ? "ДЕМО" : "СЕРВЕР")
                .font(.system(size: 9, weight: .bold))
                .padding(.horizontal, 6)
                .padding(.vertical, 4)
                .background(WatchStyle.accent.opacity(0.18), in: Capsule())
        }
    }

    private var connectionSettings: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Text("Подключение").font(.headline)
                Text("Один раз введи адрес и пароль от сайта. Затем часы подключаются сами.")
                    .font(.caption2).foregroundStyle(.secondary)
                TextField("http://IP:8000", text: $store.address)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .accessibilityLabel("Адрес сервера")
                TextField("Логин от сайта", text: $store.username)
                    .textContentType(.username)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                SecureField("Пароль от сайта", text: $store.password)
                    .textContentType(.password)
                if store.devices.count > 1 {
                    Picker("Растение", selection: $store.device) {
                        Text("Выбрать").tag("")
                        ForEach(store.devices, id: \.self) { Text($0).tag($0) }
                    }
                } else {
                    TextField("Растение: авто", text: $store.device)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                }
                if store.address.lowercased().hasPrefix("http://") {
                    Text("HTTP: пароль передаётся без шифрования. Для доступа через интернет используй HTTPS.")
                        .font(.caption2).foregroundStyle(.orange)
                }
                Button(store.isLoading ? "Подключаем…" : "Подключить и сохранить") { store.connect() }
                    .disabled(store.isLoading)
                    .tint(WatchStyle.accent)
                if let error = store.settingsError {
                    Text(error).font(.caption2).foregroundStyle(.orange)
                }
                if let settings = store.settings {
                    Text("Сохранено: \(settings.device)")
                        .font(.caption2).foregroundStyle(WatchStyle.accent)
                    Button("Показать данные") { store.showLive(); page = 0 }
                    Button("Забыть подключение", role: .destructive) { confirmingForget = true }
                }
                Button("Показать демо") { store.showDemo(); page = 0 }
                Text("Wi-Fi настраивается в самих Apple Watch. Адрес 10.0.0.20 доступен только из вашей локальной сети или VPN с маршрутом к ней.")
                    .font(.caption2).foregroundStyle(.secondary)
            }
            .padding(.horizontal, 8)
        }
    }

    private var connectionStatus: some View {
        VStack(alignment: .leading, spacing: 3) {
            if let error = store.connectionError {
                Text("Нет связи · последние данные").foregroundStyle(.orange)
                Text(error).foregroundStyle(.secondary)
            } else if store.settings == nil {
                Button("Настроить сервер") { page = 1 }
            } else if store.snapshot == nil {
                Text("Загружаем показания…")
            } else if store.snapshot?.state.latest == nil {
                Text("Нет показаний устройства").foregroundStyle(.orange)
            } else {
                Text(isFresh ? "Устройство на связи" : "Замер устарел · более 2 мин")
                    .foregroundStyle(isFresh ? WatchStyle.accent : .orange)
            }
            if !store.isDemo && store.snapshot?.state.demo == true {
                Text("На сервере включён DEMO_MODE").foregroundStyle(.orange)
            }
        }
        .font(.caption2)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var isFresh: Bool { store.snapshot?.isFresh(at: now) == true }
    private var soil: Double? { store.isDemo ? Double(scenario.moisture) : store.snapshot?.soil }
    private var low: Double { store.isDemo ? 30 : (store.snapshot?.threshold ?? 30) }
    private var high: Double { store.isDemo ? 70 : (store.snapshot?.high ?? 70) }
    private var soilText: String { soil.map { "\(Int($0.rounded()))%" } ?? "—" }
    private var soilStatus: String {
        guard let soil else { return "Нет данных" }
        if !store.isDemo && !isFresh { return "Старый замер" }
        return soil < low ? "Суховато" : soil > high ? "Влажно" : "В норме"
    }
    private var soilColor: Color {
        guard let soil else { return .gray }
        if !store.isDemo && (!isFresh || store.connectionError != nil) { return .orange }
        return soil < low || soil > high ? .orange : WatchStyle.accent
    }
    private var temperatureText: String {
        if store.isDemo { return "24,0 °C" }
        return store.snapshot?.state.latest?.temp.map {
            $0.formatted(.number.precision(.fractionLength(1)).locale(Locale(identifier: "ru_RU"))) + " °C"
        } ?? "—"
    }
    private var humidityText: String {
        if store.isDemo { return "55%" }
        return store.snapshot?.state.latest?.air_hum.map { "\(Int($0.rounded()))%" } ?? "—"
    }
    private var forecastText: String {
        let hours = store.isDemo ? Double(scenario.hoursUntilWatering) : store.snapshot?.hoursToWater(at: now)
        guard store.isDemo || store.connectionError == nil, let hours else { return "Прогноз недоступен" }
        if hours <= 0 { return "Порог уже достигнут" }
        if hours < 1 { return "Полив менее чем через час" }
        return "Полив ≈ через \(Int(hours.rounded())) ч"
    }
    private var assistantMessage: String {
        if store.isDemo { return scenario.message }
        if store.settings == nil { return "Подключи меня к серверу →" }
        if store.connectionError != nil { return "Не могу обновить данные. Проверяю связь…" }
        if !isFresh { return "Жду свежих показаний контроллера…" }
        if store.snapshot?.state.pending != nil { return "Команда полива в очереди. Жду контроллер!" }
        guard let soil else { return "Датчик почвы пока не даёт точных показаний." }
        if soil < low { return "Пить хочу! Почва подсохла." }
        if soil > high { return "Мне пока хватает воды!" }
        return "Всё хорошо! Слежу за растением."
    }

    private var metricColumns: [GridItem] {
        // Avoid squeezing Russian labels and values when larger text is enabled.
        Array(repeating: GridItem(.flexible(), alignment: .leading),
              count: dynamicTypeSize.isAccessibilitySize ? 1 : 2)
    }

    private var plantCard: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .center, spacing: 4) {
                VStack(alignment: .leading, spacing: 0) {
                    Text("Почва")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Text(soilText)
                        .font(.system(size: 35, weight: .semibold, design: .rounded))
                        .monospacedDigit()
                        .minimumScaleFactor(0.7)
                        .lineLimit(1)
                        .accessibilityLabel("Влажность почвы: \(soilText)")
                    HStack(spacing: 4) {
                        Circle().fill(soilColor).frame(width: 5, height: 5)
                        Text(soilStatus)
                            .font(.caption2)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                VStack(spacing: 0) {
                    JarvisView(isVisible: page == 0)
                        .frame(width: 64, height: 68)
                        .accessibilityHidden(true)
                    Text("Джарвис")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(WatchStyle.accent)
                }
            }
        }
        .padding(10)
        .background(WatchStyle.surface, in: RoundedRectangle(cornerRadius: 16))
    }

    private var lastWaterCard: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            HStack(spacing: 8) {
                Image(systemName: "drop.fill")
                    .foregroundStyle(WatchStyle.accent)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Последний полив")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Text(wateringAge(at: context.date))
                        .font(.footnote.weight(.semibold))
                        .monospacedDigit()
                }
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10)
            .background(WatchStyle.surface, in: RoundedRectangle(cornerRadius: 16))
        }
    }

    private func wateringAge(at date: Date) -> String {
        let elapsed: Double
        if store.isDemo {
            elapsed = date.timeIntervalSince(lastWateredAt)
        } else if let snapshot = store.snapshot, let water = snapshot.lastWater {
            elapsed = snapshot.serverTime(at: date) - water
        } else {
            return store.snapshot?.historyUnavailable == true ? "История недоступна" : "Нет записи"
        }
        let minutes = max(0, Int(elapsed / 60))
        if minutes == 0 { return "Только что" }
        if minutes < 60 { return "\(minutes) мин назад" }
        if minutes < 1440 { return "\(minutes / 60) ч назад" }
        return "\(minutes / 1440) дн. назад"
    }

    private func selectScenario(_ next: DemoScenario) {
        scenario = next
        switch next {
        case .healthy: lastWateredAt = Date().addingTimeInterval(-2 * 3600)
        case .dry: lastWateredAt = Date().addingTimeInterval(-8 * 3600)
        case .watered: lastWateredAt = Date()
        }
    }

    private func metric(_ title: String, value: String, icon: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Image(systemName: icon)
                .font(.caption2)
                .foregroundStyle(WatchStyle.accent)
            Text(value)
                .font(.system(.body, design: .rounded).weight(.semibold))
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.8)
            Text(title)
                .font(.system(size: 10))
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        .padding(8)
        .background(WatchStyle.surface, in: RoundedRectangle(cornerRadius: 14))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(title == "Темп. дома" ? "Температура дома" : "Влажность воздуха")
        .accessibilityValue(value)
    }
}

private struct JarvisView: View {
    let isVisible: Bool
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.isLuminanceReduced) private var isLuminanceReduced
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    // Keep the character identical to frontend/src/components/Jarvis.tsx.
    private static let sprite = [
        "........aa.....", ".......aa......", ".......a.......", "....bbbabb.....",
        "...cddddddb....", "..cdddddddcb...", ".cdddddddddcc..", "cddddddddddddc.",
        "cddaefaaefaddc.", "cddaffaaffaddc.", "cddgddddddgdddc", "bdddddddddddddc",
        "bddddddddddddc.", ".bddddddddddcc.", "..bbaa..aabc..."
    ]
    private static let palette: [Character: Color] = [
        "a": Color(red: 121 / 255, green: 1, blue: 49 / 255),
        "b": Color(red: 91 / 255, green: 173 / 255, blue: 37 / 255),
        "c": Color(red: 116 / 255, green: 203 / 255, blue: 58 / 255),
        "d": WatchStyle.accent,
        "e": .white, "f": .black,
        "g": Color(red: 251 / 255, green: 172 / 255, blue: 212 / 255)
    ]

    private var animating: Bool {
        isVisible && scenePhase == .active && !isLuminanceReduced && !reduceMotion
    }

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 15, paused: !animating)) { timeline in
            let seconds = animating ? timeline.date.timeIntervalSinceReferenceDate : 0
            let phase = seconds.truncatingRemainder(dividingBy: 4.2) / 4.2
            // Same 4.2-second cycle as the web: close at 95%, reopen at 100%.
            let lid = phase > 0.9 ? sin((phase - 0.9) / 0.1 * .pi) : 0
            let bob = animating ? (cos(seconds / 2.4 * 2 * .pi) - 1) * 1.5 : 0
            Canvas { context, size in
                let pixel = floor(min(size.width, size.height - 4) / 15)
                let origin = CGPoint(x: (size.width - pixel * 15) / 2,
                                     y: (size.height - pixel * 15) / 2 + bob)
                for (row, line) in Self.sprite.enumerated() {
                    for (column, symbol) in line.enumerated() {
                        guard let color = Self.palette[symbol] else { continue }
                        let rect = CGRect(x: origin.x + CGFloat(column) * pixel,
                                          y: origin.y + CGFloat(row) * pixel,
                                          width: pixel, height: pixel)
                        context.fill(Path(rect), with: .color(color), style: FillStyle(antialiased: false))
                    }
                }
                for eye in [4, 8] {
                    let rect = CGRect(x: origin.x + CGFloat(eye) * pixel,
                                      y: origin.y + 8 * pixel,
                                      width: 2 * pixel, height: 2 * pixel * max(0, lid))
                    context.fill(Path(rect), with: .color(WatchStyle.accent))
                }
            }
        }
    }
}
