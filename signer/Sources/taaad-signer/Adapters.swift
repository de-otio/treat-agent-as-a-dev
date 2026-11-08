import Foundation
import LocalAuthentication
import SignerCore

struct SystemClock: Clock {
    func now() -> Date { Date() }
}

private final class Flag: @unchecked Sendable {
    private let lock = NSLock()
    private var v = false
    func set(_ x: Bool) { lock.lock(); v = x; lock.unlock() }
    var value: Bool { lock.lock(); defer { lock.unlock() }; return v }
}

/// A fresh LAContext per prompt, no reuse window. Unavailable presence counts as a denial.
struct LAPresence: Presence {
    func approve(reason: String) -> Bool {
        let ctx = LAContext()
        ctx.touchIDAuthenticationAllowableReuseDuration = 0
        var err: NSError?
        guard ctx.canEvaluatePolicy(.deviceOwnerAuthentication, error: &err) else { return false }
        let sem = DispatchSemaphore(value: 0)
        let ok = Flag()
        ctx.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason) { r, _ in
            ok.set(r)
            sem.signal()
        }
        sem.wait()
        return ok.value
    }
}

private final class NoRedirect: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ s: URLSession, task: URLSessionTask, willPerformHTTPRedirection r: HTTPURLResponse,
                    newRequest: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil) // the JWT is never replayed to another location
    }
}

private final class Result: @unchecked Sendable {
    private let lock = NSLock()
    private var r: (Data?, URLResponse?, Error?) = (nil, nil, nil)
    func set(_ d: Data?, _ u: URLResponse?, _ e: Error?) { lock.lock(); r = (d, u, e); lock.unlock() }
    var value: (Data?, URLResponse?, Error?) { lock.lock(); defer { lock.unlock() }; return r }
}

struct URLSessionGitHub: GitHub {
    private let session: URLSession

    init() {
        let c = URLSessionConfiguration.ephemeral
        c.timeoutIntervalForRequest = 15
        c.timeoutIntervalForResource = 15
        c.urlCache = nil
        c.httpCookieStorage = nil
        c.httpShouldSetCookies = false
        session = URLSession(configuration: c, delegate: NoRedirect(), delegateQueue: nil)
    }

    func get(path: String, jwt: String) throws -> HTTPResponse { try send("GET", path, jwt, nil) }
    func post(path: String, jwt: String, body: Data) throws -> HTTPResponse { try send("POST", path, jwt, body) }

    private func send(_ method: String, _ path: String, _ jwt: String, _ body: Data?) throws -> HTTPResponse {
        guard let url = URL(string: SignerInfo.apiBase + path) else { throw SignerError.internalError("invalid request path") }
        var req = URLRequest(url: url, timeoutInterval: 15)
        req.httpMethod = method
        req.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        req.setValue("2022-11-28", forHTTPHeaderField: "X-GitHub-Api-Version")
        req.setValue(SignerInfo.userAgent, forHTTPHeaderField: "User-Agent")
        req.setValue("Bearer \(jwt)", forHTTPHeaderField: "Authorization")
        if let body, !body.isEmpty {
            req.httpBody = body
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let sem = DispatchSemaphore(value: 0)
        let box = Result()
        session.dataTask(with: req) { d, u, e in box.set(d, u, e); sem.signal() }.resume()
        sem.wait()
        let (data, resp, err) = box.value
        if let err {
            throw SignerError.github(status: 0, message: "transport error: \((err as NSError).domain) \((err as NSError).code)")
        }
        guard let http = resp as? HTTPURLResponse else {
            throw SignerError.github(status: 0, message: "no HTTP response")
        }
        return HTTPResponse(status: http.statusCode, body: data ?? Data())
    }
}

/// Exclusive flock on a read-only descriptor of the helper's own executable, so every instance contends on one file.
struct FlockLock: Lock {
    static func executablePath() -> String? {
        if let p = Bundle.main.executablePath { return p }
        var size: UInt32 = 0
        _ = _NSGetExecutablePath(nil, &size)
        var buf = [CChar](repeating: 0, count: Int(size))
        guard _NSGetExecutablePath(&buf, &size) == 0 else { return nil }
        return String(decoding: buf.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }, as: UTF8.self)
    }

    func withExclusive<T>(_ body: () throws -> T) throws -> T {
        guard let path = FlockLock.executablePath() else { throw SignerError.internalError("cannot locate own executable for locking") }
        let fd = open(path, O_RDONLY | O_CLOEXEC)
        guard fd >= 0 else { throw SignerError.internalError("cannot open own executable for locking") }
        defer { close(fd) }
        let deadline = Date().addingTimeInterval(120)
        while flock(fd, LOCK_EX | LOCK_NB) != 0 {
            guard errno == EWOULDBLOCK || errno == EINTR else { throw SignerError.internalError("flock failed") }
            if Date() >= deadline { throw SignerError.rateLimited(retryAfter: 60) }
            usleep(100_000)
        }
        defer { flock(fd, LOCK_UN) }
        return try body()
    }
}
