import Foundation
import ImageIO
import UniformTypeIdentifiers

do {
    guard CommandLine.arguments.count == 3,
          let source=CGImageSourceCreateWithURL(URL(fileURLWithPath:CommandLine.arguments[1]) as CFURL,nil) else { throw NSError(domain:"usage",code:1) }
    let count=CGImageSourceGetCount(source)
    guard count>1,count<=300 else { throw NSError(domain:"frame-budget",code:2) }
    let directory=URL(fileURLWithPath:CommandLine.arguments[2],isDirectory:true)
    var frames:[[String:Any]]=[]
    for index in 0..<count {
        try autoreleasepool {
            guard let image=CGImageSourceCreateImageAtIndex(source,index,nil),image.width*image.height<=40_000_000 else { throw NSError(domain:"frame-size",code:3) }
            let url=directory.appendingPathComponent(String(format:"frame-%04d.png",index+1))
            guard !FileManager.default.fileExists(atPath:url.path),let destination=CGImageDestinationCreateWithURL(url as CFURL,UTType.png.identifier as CFString,1,nil) else { throw NSError(domain:"frame-output",code:4) }
            CGImageDestinationAddImage(destination,image,nil)
            guard CGImageDestinationFinalize(destination) else { throw NSError(domain:"frame-save",code:5) }
            frames.append(["frame_index":index+1,"file":url.path,"width":image.width,"height":image.height])
        }
    }
    FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject:["frame_count":count,"frames":frames],options:[.sortedKeys]))
} catch {
    FileHandle.standardError.write(Data("Local animation frame extraction failed: \(error)\n".utf8));exit(1)
}
