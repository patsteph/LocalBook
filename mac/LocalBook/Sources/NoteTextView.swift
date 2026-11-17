import AppKit
import LocalBookCore
import Observation
import SwiftUI

/// The formatting commands of the note editor, aimed at whichever text view is showing.
@MainActor @Observable
final class NoteEditorBridge {
    @ObservationIgnored weak var textView: NSTextView?

    func perform(_ action: MarkdownEdit.Action) {
        guard let tv = textView, let edit = MarkdownEdit.edit(tv.string, selection: tv.selectedRange(), action: action) else { return }
        apply(edit, to: tv)
    }

    /// A link to whatever address the clipboard holds, or a placeholder to type over.
    func link() {
        let pasted = NSPasteboard.general.string(forType: .string)?.trimmingCharacters(in: .whitespacesAndNewlines)
        let address = pasted.flatMap { s in (s.hasPrefix("http://") || s.hasPrefix("https://")) && !s.contains(" ") ? s : nil }
        perform(.link(address))
    }

    func apply(_ edit: MarkdownEdit.Edit, to tv: NSTextView) {
        guard tv.shouldChangeText(in: edit.range, replacementString: edit.replacement) else { return }
        tv.textStorage?.replaceCharacters(in: edit.range, with: edit.replacement)
        tv.didChangeText()
        tv.setSelectedRange(edit.selection)
        tv.scrollRangeToVisible(edit.selection)
    }
}

/// A TextKit 2 text view that edits a note's Markdown and styles it in place (`MarkdownEdit.spans`): headings large,
/// bold bold, markers tinted, the syntax itself dimmed. The text stays Markdown throughout, so what is saved is
/// exactly what was typed. Return inside a list carries the list on; clicking a task box ticks it.
struct NoteTextView: NSViewRepresentable {
    @Binding var text: String
    let bridge: NoteEditorBridge
    var placeholder = ""

    func makeNSView(context: Context) -> NSScrollView {
        // The factory's text view is TextKit 2 on current macOS and comes wired to its scroll view.
        let scroll = NSTextView.scrollableTextView()
        guard let tv = scroll.documentView as? NSTextView else { return scroll }
        tv.isRichText = false
        tv.allowsUndo = true
        tv.isAutomaticQuoteSubstitutionEnabled = false
        tv.isAutomaticDashSubstitutionEnabled = false
        tv.isAutomaticTextReplacementEnabled = false
        tv.isAutomaticSpellingCorrectionEnabled = true
        tv.isContinuousSpellCheckingEnabled = true
        tv.usesFindBar = true
        tv.isIncrementalSearchingEnabled = true
        tv.importsGraphics = false
        tv.textContainerInset = NSSize(width: 14, height: 12)
        tv.font = Styler.body
        tv.textColor = .labelColor
        tv.typingAttributes = [.font: Styler.body, .foregroundColor: NSColor.labelColor]
        tv.delegate = context.coordinator
        tv.string = text
        scroll.hasVerticalScroller = true
        scroll.drawsBackground = true
        scroll.borderType = .noBorder
        context.coordinator.textView = tv
        context.coordinator.placeholderLabel.stringValue = placeholder
        context.coordinator.placeholderLabel.isHidden = !text.isEmpty
        tv.addSubview(context.coordinator.placeholderLabel)
        context.coordinator.restyle()
        bridge.textView = tv
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let tv = scroll.documentView as? NSTextView else { return }
        bridge.textView = tv
        context.coordinator.text = $text
        if tv.string != text {                              // dictation or a picture's text arrived from outside
            let selection = tv.selectedRange()
            tv.string = text
            let end = (text as NSString).length
            tv.setSelectedRange(NSRange(location: min(selection.location, end), length: 0))
            context.coordinator.restyle()
        }
        context.coordinator.placeholderLabel.isHidden = !text.isEmpty
    }

    func makeCoordinator() -> Coordinator { Coordinator(text: $text) }

    @MainActor
    final class Coordinator: NSObject, NSTextViewDelegate {
        var text: Binding<String>
        weak var textView: NSTextView?
        let placeholderLabel: NSTextField = {
            let l = NSTextField(labelWithString: "")
            l.font = Styler.body
            l.textColor = .tertiaryLabelColor
            l.frame = NSRect(x: 19, y: 12, width: 600, height: 22)
            l.isHidden = true
            return l
        }()

        init(text: Binding<String>) { self.text = text; super.init() }

        func textDidChange(_ notification: Notification) {
            guard let tv = textView else { return }
            text.wrappedValue = tv.string
            placeholderLabel.isHidden = !tv.string.isEmpty
            restyle()
        }

        func textView(_ textView: NSTextView, doCommandBy commandSelector: Selector) -> Bool {
            guard commandSelector == #selector(NSResponder.insertNewline(_:)),
                  let edit = MarkdownEdit.newline(textView.string, selection: textView.selectedRange()) else { return false }
            guard textView.shouldChangeText(in: edit.range, replacementString: edit.replacement) else { return true }
            textView.textStorage?.replaceCharacters(in: edit.range, with: edit.replacement)
            textView.didChangeText()
            textView.setSelectedRange(edit.selection)
            return true
        }

        func textView(_ textView: NSTextView, clickedOnLink link: Any, at charIndex: Int) -> Bool {
            if let url = link as? URL { NSWorkspace.shared.open(url); return true }
            if let s = link as? String, let url = URL(string: s) { NSWorkspace.shared.open(url); return true }
            return false
        }

        /// A click on a task box (`[ ]`) ticks or unticks it. The box keeps its length, so the click's own selection
        /// handling is unaffected.
        func textViewDidChangeSelection(_ notification: Notification) {
            guard let tv = textView, let event = NSApp.currentEvent, event.type == .leftMouseDown, event.clickCount == 1,
                  tv.selectedRange().length == 0 else { return }
            let location = tv.selectedRange().location
            // Only a click inside the `[ ]` of a task toggles it, so ordinary clicks on the text are left alone.
            let ns = tv.string as NSString
            guard location < ns.length else { return }
            let lineRange = ns.lineRange(for: NSRange(location: location, length: 0))
            let line = ns.substring(with: lineRange)
            guard let re = try? NSRegularExpression(pattern: #"^\s*[-*+] \[[ xX]\]"#),
                  let m = re.firstMatch(in: line, range: NSRange(location: 0, length: (line as NSString).length)) else { return }
            let boxStart = lineRange.location + m.range.length - 3
            guard location >= boxStart, location <= boxStart + 3, let edit = MarkdownEdit.toggleTask(tv.string, at: location) else { return }
            guard tv.shouldChangeText(in: edit.range, replacementString: edit.replacement) else { return }
            tv.textStorage?.replaceCharacters(in: edit.range, with: edit.replacement)
            tv.didChangeText()
            tv.setSelectedRange(NSRange(location: lineRange.location + lineRange.length - (lineRange.length > 0 && line.hasSuffix("\n") ? 1 : 0), length: 0))
        }

        /// Attributes over the whole text from the Markdown, after every change. Notes are short, so the whole pass
        /// is cheap; it never touches the characters, so undo is unaffected.
        func restyle() {
            guard let tv = textView, let storage = tv.textStorage else { return }
            let whole = NSRange(location: 0, length: storage.length)
            storage.beginEditing()
            storage.setAttributes([.font: Styler.body, .foregroundColor: NSColor.labelColor], range: whole)
            for span in MarkdownEdit.spans(tv.string) {
                guard span.range.location + span.range.length <= storage.length else { continue }
                storage.addAttributes(Styler.attributes(span.style, over: storage, in: span.range), range: span.range)
            }
            storage.endEditing()
            tv.typingAttributes = [.font: Styler.body, .foregroundColor: NSColor.labelColor]
        }
    }

    /// What each Markdown style looks like.
    @MainActor enum Styler {
        static let body = NSFont.systemFont(ofSize: 15)
        static let mono = NSFont.monospacedSystemFont(ofSize: 13.5, weight: .regular)

        static func attributes(_ style: MarkdownEdit.Style, over storage: NSTextStorage, in range: NSRange) -> [NSAttributedString.Key: Any] {
            switch style {
            case .heading(let n):
                let size: CGFloat = n == 1 ? 26 : (n == 2 ? 21 : 17)
                return [.font: NSFont.systemFont(ofSize: size, weight: n <= 2 ? .bold : .semibold)]
            case .bold:
                return [.font: with(trait: .boldFontMask, in: storage, at: range.location)]
            case .italic:
                return [.font: with(trait: .italicFontMask, in: storage, at: range.location)]
            case .code:
                return [.font: mono, .backgroundColor: NSColor.quaternaryLabelColor.withAlphaComponent(0.18)]
            case .codeBlock:
                return [.font: mono, .foregroundColor: NSColor.secondaryLabelColor]
            case .quote:
                return [.foregroundColor: NSColor.secondaryLabelColor, .font: with(trait: .italicFontMask, in: storage, at: range.location)]
            case .listMarker:
                return [.foregroundColor: NSColor.controlAccentColor]
            case .taskDone:
                return [.foregroundColor: NSColor.secondaryLabelColor, .strikethroughStyle: NSUnderlineStyle.single.rawValue]
            case .link:
                return [.foregroundColor: NSColor.linkColor, .underlineStyle: NSUnderlineStyle.single.rawValue]
            case .syntax:
                return [.foregroundColor: NSColor.tertiaryLabelColor]
            }
        }

        /// The font already at `location` (a heading's, say) with `trait` added, so bold inside a heading stays big.
        static func with(trait: NSFontTraitMask, in storage: NSTextStorage, at location: Int) -> NSFont {
            let current = (location < storage.length ? storage.attribute(.font, at: location, effectiveRange: nil) as? NSFont : nil) ?? body
            return NSFontManager.shared.convert(current, toHaveTrait: trait)
        }
    }
}
