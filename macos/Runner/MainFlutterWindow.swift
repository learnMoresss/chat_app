import Cocoa
import FlutterMacOS

class MainFlutterWindow: NSWindow {
  override func awakeFromNib() {
    let flutterViewController = FlutterViewController()
    let windowFrame = self.frame
    self.contentViewController = flutterViewController
    self.setContentSize(NSSize(width: 390, height: 844))
    self.setFrameOrigin(windowFrame.origin)
    let phoneSize = self.frame.size
    self.minSize = phoneSize
    self.maxSize = phoneSize
    self.title = "微信"

    RegisterGeneratedPlugins(registry: flutterViewController)

    super.awakeFromNib()
  }
}
