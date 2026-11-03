import Foundation
import Vision
import ImageIO

// Local first-frame OCR and an explicitly versioned visual feature vector.
do {
    guard CommandLine.arguments.count == 2 else { throw NSError(domain: "usage", code: 1) }
    let url = URL(fileURLWithPath: CommandLine.arguments[1])
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
          let width = properties[kCGImagePropertyPixelWidth] as? Int,
          let height = properties[kCGImagePropertyPixelHeight] as? Int,
          width > 0, height > 0, width <= 20000, height <= 20000,
          width * height <= 40_000_000 else { throw NSError(domain: "image-budget", code: 2) }
    let text = VNRecognizeTextRequest()
    text.recognitionLevel = .accurate
    text.recognitionLanguages = ["zh-Hans", "en-US"]
    text.usesLanguageCorrection = true
    let feature = VNGenerateImageFeaturePrintRequest()
    feature.revision = VNGenerateImageFeaturePrintRequestRevision2
    feature.imageCropAndScaleOption = .scaleFill
    try VNImageRequestHandler(url: url).perform([text, feature])
    guard let print = feature.results?.first, print.elementType == .float else { throw NSError(domain: "feature", code: 3) }
    let vector: [Float] = print.data.withUnsafeBytes { Array($0.bindMemory(to: Float.self)) }
    guard vector.count == print.elementCount, vector.allSatisfy({ $0.isFinite }) else { throw NSError(domain: "vector", code: 4) }
    let lines: [[String: Any]] = (text.results ?? []).compactMap { observation in
        guard let value = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return ["text": value.string, "confidence": value.confidence,
                "bbox": [box.minX, 1-box.maxY, box.width, box.height]]
    }
    let payload: [String: Any] = ["lines": lines, "vector": vector, "dimension": vector.count,
        "feature_revision": feature.revision, "ocr_revision": text.revision,
        "frame_count": CGImageSourceGetCount(source), "frames_processed": 1,
        "width": width, "height": height]
    FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]))
} catch {
    FileHandle.standardError.write(Data("Local image OCR/feature extraction failed: \(error)\n".utf8))
    exit(1)
}
