import Foundation
import Vision
import ImageIO

// macOS Vision operates on the local image; no network or model download.
do {
    guard CommandLine.arguments.count == 2 else { throw NSError(domain: "usage", code: 1) }
    let url = URL(fileURLWithPath: CommandLine.arguments[1])
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
          let width = properties[kCGImagePropertyPixelWidth] as? Int,
          let height = properties[kCGImagePropertyPixelHeight] as? Int,
          width > 0, height > 0, width <= 20000, height <= 20000,
          width * height <= 40_000_000 else { throw NSError(domain: "image-budget", code: 2) }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = true
    try VNImageRequestHandler(url: url).perform([request])
    let items: [[String: Any]] = (request.results ?? []).compactMap { observation in
        guard let text = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return ["text": text.string, "confidence": text.confidence,
                "bbox": [box.minX, 1-box.maxY, box.width, box.height]]
    }
    let data = try JSONSerialization.data(withJSONObject: ["engine": "apple_vision", "lines": items, "frame_count": CGImageSourceGetCount(source), "frames_processed": 1], options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
} catch {
    FileHandle.standardError.write(Data("Local Vision OCR failed\n".utf8))
    exit(1)
}
