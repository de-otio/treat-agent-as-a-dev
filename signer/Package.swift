// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "taaad-signer",
    platforms: [.macOS(.v13)],
    targets: [
        // Pure decisions plus protocols. No keychain, no network, no UI.
        .target(name: "SignerCore", path: "Sources/SignerCore"),
        // The imperative shell: keychain, LAContext, URLSession, main. No decisions.
        .executableTarget(
            name: "taaad-signer",
            dependencies: ["SignerCore"],
            path: "Sources/taaad-signer"
        ),
        .testTarget(
            name: "SignerCoreTests",
            dependencies: ["SignerCore"],
            path: "Tests/SignerCoreTests"
        ),
    ]
)
