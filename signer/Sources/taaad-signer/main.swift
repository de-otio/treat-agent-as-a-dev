import Foundation
import SignerCore

/// Reads at most 64 KiB + 1 byte; the core rejects anything over 64 KiB.
func readStdinLimited() throws -> Data {
    var data = Data()
    let limit = PEM.maxInputBytes + 1
    while data.count < limit {
        guard let chunk = try FileHandle.standardInput.read(upToCount: min(8192, limit - data.count)), !chunk.isEmpty else { break }
        data.append(chunk)
    }
    return data
}

let environment = Environment(store: KeychainStore(), keys: KeychainKeyStore(), clock: SystemClock(),
                              presence: LAPresence(), github: URLSessionGitHub(), lock: FlockLock())
let result = run(arguments: Array(CommandLine.arguments.dropFirst()), readStdin: readStdinLimited, environment: environment)
if !result.stdout.isEmpty { FileHandle.standardOutput.write(Data(result.stdout.utf8)) }
if !result.stderr.isEmpty { FileHandle.standardError.write(Data(result.stderr.utf8)) }
exit(result.exitCode)
