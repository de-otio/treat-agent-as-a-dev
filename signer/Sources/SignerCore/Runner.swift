import Foundation

public struct ExecResult: Equatable, Sendable {
    public let exitCode: Int32
    public let stdout: String
    public let stderr: String
}

/// Argv in, exit code and the two output streams out. Success: one JSON line on stdout and nothing on stderr.
/// Failure: one JSON line on stderr and nothing on stdout. `readStdin` is called only for `import`.
public func run(arguments: [String], readStdin: () throws -> Data, environment: Environment) -> ExecResult {
    do {
        let command = try Arguments.parse(arguments)
        let out = try Signer(environment: environment).run(command, readStdin: readStdin)
        return ExecResult(exitCode: 0, stdout: out.serialized() + "\n", stderr: "")
    } catch let e as SignerError {
        return ExecResult(exitCode: e.exitCode, stdout: "", stderr: e.json.serialized() + "\n")
    } catch {
        let e = SignerError.internalError("unexpected failure (\(type(of: error)))")
        return ExecResult(exitCode: e.exitCode, stdout: "", stderr: e.json.serialized() + "\n")
    }
}
