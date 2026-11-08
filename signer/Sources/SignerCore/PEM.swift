import Foundation

/// PEM and minimal DER reading. Output is the PKCS#1 `RSAPrivateKey` DER, or `SignerError.badKey`.
public enum PEM {
    public static let maxInputBytes = 64 * 1024
    static let rsaOID: [UInt8] = [0x2A, 0x86, 0x48, 0x86, 0xF7, 0x0D, 0x01, 0x01, 0x01]

    public static func parsePrivateKey(_ input: Data) throws -> Data {
        guard input.count <= maxInputBytes else { throw SignerError.badKey("input larger than 64 KiB") }
        guard let text = String(data: input, encoding: .utf8) else { throw SignerError.badKey("input is not PEM text") }
        var blocks: [(label: String, body: [String])] = []
        var current: (label: String, body: [String])?
        for raw in text.split(whereSeparator: \.isNewline) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty { continue }
            if var c = current {
                if line == "-----END \(c.label)-----" {
                    blocks.append(c); current = nil
                } else if line.hasPrefix("-----") {
                    throw SignerError.badKey("malformed PEM")
                } else {
                    c.body.append(line); current = c
                }
            } else if line.hasPrefix("-----BEGIN "), line.hasSuffix("-----"), line.count > 16 {
                current = (String(line.dropFirst(11).dropLast(5)), [])
            } else {
                throw SignerError.badKey("text outside a PEM block")
            }
        }
        guard current == nil else { throw SignerError.badKey("unterminated PEM block") }
        guard blocks.count == 1 else {
            throw SignerError.badKey(blocks.isEmpty ? "no PEM block" : "more than one PEM block")
        }
        let block = blocks[0]
        if block.label == "ENCRYPTED PRIVATE KEY" || block.body.contains(where: { $0.contains(":") }) {
            throw SignerError.badKey("encrypted keys are not supported")
        }
        guard block.label == "RSA PRIVATE KEY" || block.label == "PRIVATE KEY" else {
            throw SignerError.badKey("not an RSA private key")
        }
        guard let der = Data(base64Encoded: block.body.joined()), !der.isEmpty else {
            throw SignerError.badKey("invalid base64 in PEM")
        }
        let bytes = [UInt8](der)
        let pkcs1 = block.label == "PRIVATE KEY" ? try unwrapPKCS8(bytes) : bytes
        let bits = try validatePKCS1(pkcs1)
        guard bits >= 2048 else { throw SignerError.badKey("RSA key is \(bits) bits; at least 2048 required") }
        return Data(pkcs1)
    }

    // MARK: DER

    struct Element { let tag: UInt8; let content: Range<Int>; let end: Int }

    static func read(_ b: [UInt8], at i: Int) throws -> Element {
        func bad() -> SignerError { SignerError.badKey("malformed DER") }
        guard i + 2 <= b.count else { throw bad() }
        let tag = b[i]
        let first = Int(b[i + 1])
        var len = 0
        var p = i + 2
        if first < 0x80 {
            len = first
        } else {
            let n = first & 0x7F
            guard n >= 1, n <= 4, p + n <= b.count else { throw bad() }
            for k in 0..<n { len = (len << 8) | Int(b[p + k]) }
            p += n
        }
        guard len <= b.count - p else { throw bad() }
        return Element(tag: tag, content: p..<(p + len), end: p + len)
    }

    static func unwrapPKCS8(_ b: [UInt8]) throws -> [UInt8] {
        func bad(_ m: String = "not an RSA PKCS#8 key") -> SignerError { SignerError.badKey(m) }
        let outer = try read(b, at: 0)
        guard outer.tag == 0x30, outer.end == b.count else { throw bad("malformed DER") }
        let version = try read(b, at: outer.content.lowerBound)
        guard version.tag == 0x02, Array(b[version.content]) == [0x00] else { throw bad() }
        let alg = try read(b, at: version.end)
        guard alg.tag == 0x30 else { throw bad() }
        let oid = try read(b, at: alg.content.lowerBound)
        guard oid.tag == 0x06 else { throw bad() }
        guard Array(b[oid.content]) == rsaOID else { throw bad("not an RSA key") }
        if oid.end != alg.end {
            let params = try read(b, at: oid.end)
            guard params.tag == 0x05, params.content.isEmpty, params.end == alg.end else { throw bad() }
        }
        let octets = try read(b, at: alg.end)
        guard octets.tag == 0x04, octets.end == outer.end else { throw bad() }
        return Array(b[octets.content])
    }

    /// Checks the shape of `RSAPrivateKey` (version 0 and eight more integers) and returns the modulus size in bits.
    static func validatePKCS1(_ b: [UInt8]) throws -> Int {
        func bad() -> SignerError { SignerError.badKey("not a PKCS#1 RSA private key") }
        let seq = try read(b, at: 0)
        guard seq.tag == 0x30, seq.end == b.count else { throw bad() }
        var pos = seq.content.lowerBound
        var ints: [Element] = []
        while pos < seq.end {
            let e = try read(b, at: pos)
            guard e.tag == 0x02, e.end <= seq.end, !e.content.isEmpty else { throw bad() }
            ints.append(e); pos = e.end
        }
        guard ints.count == 9, Array(b[ints[0].content]) == [0x00] else { throw bad() }
        let mod = Array(b[ints[1].content])
        guard mod[0] & 0x80 == 0 else { throw bad() }
        let trimmed = mod.drop(while: { $0 == 0 })
        guard let head = trimmed.first else { throw bad() }
        return (trimmed.count - 1) * 8 + (8 - head.leadingZeroBitCount)
    }
}
