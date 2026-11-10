import UIKit
import Capacitor

// The screen that shows Pomona. Capacitor's own view controller, plus the
// plugins that live inside this app rather than in node_modules (Capacitor
// only finds npm plugins by itself). Main.storyboard points here.
class MainViewController: CAPBridgeViewController {
    override open func capacitorDidLoad() {
        // Speech-to-text on Apple's on-device engine (PomonaSpeechPlugin.swift).
        bridge?.registerPluginInstance(PomonaSpeechPlugin())
    }
}
