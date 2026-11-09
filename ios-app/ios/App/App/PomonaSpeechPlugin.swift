import Foundation
import AVFoundation
import UIKit
import Capacitor
import Speech

// Pomona's own speech-to-text for the iPhone app (voice research,
// 2026-11-09 — Emily: "it's almost unusable it's so bad").
//
// The website's mic (static/dictation.js) used Safari's Web Speech API,
// which on iOS is Apple's OLD recognizer, one phrase at a time: it stops at
// the first pause and gets about one word in six wrong on everyday speech.
// iOS 26 brought a new on-device engine, SpeechAnalyzer, at roughly a
// quarter of those mistakes. This plugin gives the page that engine:
//
//   PomonaSpeech.isAvailable()  -> { available }   never asks permission
//   PomonaSpeech.start()        -> resolves once listening; rejects with
//                                  code "denied" (microphone off), "busy",
//                                  "unavailable" or "failed"
//   PomonaSpeech.stop()         -> { text }        the finished words
//   event "partial"             -> { text }        all the words so far
//   event "ended"               -> { text, reason } it stopped by itself:
//                                  "interrupted" (a call, Siri), "background"
//                                  or "limit" (two minutes)
//
// Free, and nothing leaves the phone. It keeps listening through pauses
// until the mic is tapped again. On iOS 15–25 isAvailable says false and
// the page uses the keyboard's mic instead.
//
// Registered in MainViewController.swift (a plugin inside the app itself
// isn't found by Capacitor's automatic plugin list, only npm ones are).
@objc(PomonaSpeechPlugin)
public class PomonaSpeechPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "PomonaSpeechPlugin"
    public let jsName = "PomonaSpeech"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "isAvailable", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "start", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "stop", returnType: CAPPluginReturnPromise),
    ]

    // A PomonaDictation while listening (typed loosely: that class only
    // exists on iOS 26). Only touched on the main thread.
    private var current: AnyObject?

    @objc func isAvailable(_ call: CAPPluginCall) {
        guard #available(iOS 26.0, *) else {
            call.resolve(["available": false])
            return
        }
        Task { @MainActor in
            let ok = await PomonaDictation.chooseEngine() != nil
            call.resolve(["available": ok])
        }
    }

    @objc func start(_ call: CAPPluginCall) {
        guard #available(iOS 26.0, *) else {
            call.reject("Needs iOS 26 or newer", "unavailable")
            return
        }
        Task { @MainActor in
            if self.current != nil {
                call.reject("Already listening", "busy")
                return
            }
            guard await Self.microphoneAllowed() else {
                call.reject("The microphone is off for Pomona", "denied")
                return
            }
            let dictation = PomonaDictation(
                onText: { [weak self] text in
                    self?.notifyListeners("partial", data: ["text": text])
                },
                onEnded: { [weak self] text, reason in
                    self?.current = nil
                    self?.notifyListeners("ended", data: ["text": text, "reason": reason])
                }
            )
            self.current = dictation
            do {
                try await dictation.start()
                call.resolve()
            } catch {
                self.current = nil
                await dictation.cancel()
                call.reject("Couldn't start listening", "failed", error)
            }
        }
    }

    @objc func stop(_ call: CAPPluginCall) {
        guard #available(iOS 26.0, *) else {
            call.resolve(["text": ""])
            return
        }
        Task { @MainActor in
            guard let dictation = self.current as? PomonaDictation else {
                call.resolve(["text": ""])
                return
            }
            self.current = nil
            let text = await dictation.stop()
            call.resolve(["text": text])
        }
    }

    // Asks for the microphone the first time only; iOS shows the
    // NSMicrophoneUsageDescription line from Info.plist. Only ever called
    // on iOS 26 (start() checks first).
    @available(iOS 17.0, *)
    private static func microphoneAllowed() async -> Bool {
        switch AVAudioApplication.shared.recordPermission {
        case .granted:
            return true
        case .denied:
            return false
        default:
            return await AVAudioApplication.requestRecordPermission()
        }
    }
}

// One listening session: microphone -> SpeechAnalyzer -> text.
@available(iOS 26.0, *)
@MainActor
final class PomonaDictation {
    enum Engine {
        // The long-form model the accuracy numbers are for; not on every
        // phone (SpeechTranscriber.isAvailable).
        case transcriber(Locale)
        // The dictation model, on every phone that runs iOS 26.
        case dictation(Locale)
    }

    // Pomona's copy is written in US English (decided 2026-09-17), and the
    // website's mic listens in en-US too.
    static let language = Locale(identifier: "en-US")
    static let maxSeconds: UInt64 = 120

    private let onText: (String) -> Void
    private let onEnded: (String, String) -> Void
    private let engine = AVAudioEngine()
    private var analyzer: SpeechAnalyzer?
    private var input: AsyncStream<AnalyzerInput>.Continuation?
    private var resultsTask: Task<Void, Never>?
    private var limitTask: Task<Void, Never>?
    private var observers: [NSObjectProtocol] = []
    private var finalized = ""
    private var volatile = ""
    private var running = false

    init(onText: @escaping (String) -> Void, onEnded: @escaping (String, String) -> Void) {
        self.onText = onText
        self.onEnded = onEnded
    }

    var text: String {
        PomonaDictation.join(finalized, volatile).trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static func chooseEngine() async -> Engine? {
        if SpeechTranscriber.isAvailable,
           let locale = await SpeechTranscriber.supportedLocale(equivalentTo: language) {
            return .transcriber(locale)
        }
        if let locale = await DictationTranscriber.supportedLocale(equivalentTo: language) {
            return .dictation(locale)
        }
        return nil
    }

    func start() async throws {
        guard let choice = await PomonaDictation.chooseEngine() else {
            throw PomonaSpeechError.unsupported
        }
        let module: any SpeechModule
        switch choice {
        case .transcriber(let locale):
            let transcriber = SpeechTranscriber(locale: locale, preset: .progressiveTranscription)
            module = transcriber
            resultsTask = Task { [weak self] in
                do {
                    for try await result in transcriber.results {
                        self?.take(String(result.text.characters), isFinal: result.isFinal)
                    }
                } catch {}
            }
        case .dictation(let locale):
            let transcriber = DictationTranscriber(locale: locale, preset: .progressiveLongDictation)
            module = transcriber
            resultsTask = Task { [weak self] in
                do {
                    for try await result in transcriber.results {
                        self?.take(String(result.text.characters), isFinal: result.isFinal)
                    }
                } catch {}
            }
        }

        // The model is usually on the phone already (the keyboard uses it);
        // if not, iOS fetches it once, here.
        if let request = try await AssetInventory.assetInstallationRequest(supporting: [module]) {
            try await request.downloadAndInstall()
        }

        guard let format = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [module]) else {
            throw PomonaSpeechError.noAudioFormat
        }
        let analyzer = SpeechAnalyzer(modules: [module])
        self.analyzer = analyzer
        try await analyzer.prepareToAnalyze(in: format)

        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.record, mode: .measurement, options: [.duckOthers])
        try session.setActive(true)

        let (stream, continuation) = AsyncStream<AnalyzerInput>.makeStream()
        input = continuation
        try PomonaDictation.feedMicrophone(of: engine, as: format, into: continuation)
        engine.prepare()
        try engine.start()
        try await analyzer.start(inputSequence: stream)
        running = true

        watchForInterruptions()
        limitTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: PomonaDictation.maxSeconds * 1_000_000_000)
            guard !Task.isCancelled else { return }
            await self?.endByItself("limit")
        }
    }

    // Tapped again: finish the last words and hand back everything.
    func stop() async -> String {
        await finish()
        return text
    }

    // Start failed part way: let go of the mic without waiting on words.
    func cancel() async {
        teardownAudio()
        input?.finish()
        await analyzer?.cancelAndFinishNow()
        resultsTask?.cancel()
        limitTask?.cancel()
        removeObservers()
    }

    private func endByItself(_ reason: String) async {
        guard running else { return }
        await finish()
        onEnded(text, reason)
    }

    private func finish() async {
        guard running else { return }
        running = false
        limitTask?.cancel()
        removeObservers()
        teardownAudio()
        input?.finish()
        try? await analyzer?.finalizeAndFinishThroughEndOfInput()
        await resultsTask?.value
    }

    private func take(_ piece: String, isFinal: Bool) {
        if isFinal {
            finalized = PomonaDictation.join(finalized, piece)
            volatile = ""
        } else {
            volatile = piece
        }
        onText(text)
    }

    private static func join(_ a: String, _ b: String) -> String {
        if a.isEmpty { return b }
        if b.isEmpty { return a }
        if a.hasSuffix(" ") || b.hasPrefix(" ") { return a + b }
        return a + " " + b
    }

    private func teardownAudio() {
        if engine.isRunning { engine.stop() }
        engine.inputNode.removeTap(onBus: 0)
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    private func watchForInterruptions() {
        let center = NotificationCenter.default
        observers.append(center.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in await self?.endByItself("interrupted") }
        })
        observers.append(center.addObserver(forName: UIApplication.didEnterBackgroundNotification, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in await self?.endByItself("background") }
        })
    }

    private func removeObservers() {
        observers.forEach { NotificationCenter.default.removeObserver($0) }
        observers.removeAll()
    }

    // The microphone tap runs on the audio thread, never the main one, so
    // it is set up outside the main actor and touches nothing of the session
    // but the stream it feeds.
    nonisolated private static func feedMicrophone(of engine: AVAudioEngine, as format: AVAudioFormat,
                                                   into continuation: AsyncStream<AnalyzerInput>.Continuation) throws {
        let node = engine.inputNode
        let micFormat = node.outputFormat(forBus: 0)
        guard micFormat.sampleRate > 0, micFormat.channelCount > 0 else {
            throw PomonaSpeechError.noMicrophone
        }
        let converter = micFormat == format ? nil : AVAudioConverter(from: micFormat, to: format)
        if micFormat != format && converter == nil {
            throw PomonaSpeechError.noAudioFormat
        }
        node.installTap(onBus: 0, bufferSize: 4096, format: micFormat) { buffer, _ in
            if let converter {
                if let converted = convert(buffer, with: converter, to: format) {
                    continuation.yield(AnalyzerInput(buffer: converted))
                }
            } else {
                continuation.yield(AnalyzerInput(buffer: buffer))
            }
        }
    }

    nonisolated private static func convert(_ buffer: AVAudioPCMBuffer, with converter: AVAudioConverter,
                                            to format: AVAudioFormat) -> AVAudioPCMBuffer? {
        let ratio = format.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount((Double(buffer.frameLength) * ratio).rounded(.up)) + 1
        guard let out = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: capacity) else { return nil }
        var handedOver = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if handedOver {
                status.pointee = .noDataNow
                return nil
            }
            handedOver = true
            status.pointee = .haveData
            return buffer
        }
        return error == nil && out.frameLength > 0 ? out : nil
    }
}

enum PomonaSpeechError: Error {
    case unsupported
    case noAudioFormat
    case noMicrophone
}
