// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "PlantWatchCore",
    platforms: [.macOS(.v13)],
    targets: [
        .target(name: "PlantWatchCore", path: "PlantWatch",
                exclude: ["PlantWatchApp.swift", "WatchStore.swift", "Info.plist"],
                sources: ["PlantAPI.swift"]),
        .testTarget(name: "PlantWatchCoreTests", dependencies: ["PlantWatchCore"], path: "Tests")
    ]
)
