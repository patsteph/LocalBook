import AppIntents
import SwiftUI
import WidgetKit

/// LocalBook's widgets: one job, start a meeting recording. A tap opens the app on the recorder, which begins
/// recording at once; the answer model is never loaded on that path, so it is quick.
@main
struct LocalBookWidgets: WidgetBundle {
    var body: some Widget {
        RecordMeetingWidget()
        RecordMeetingControl()
    }
}

// MARK: home screen and Lock Screen

struct RecordMeetingWidget: Widget {
    static let kind = "com.patsteph.localbook.ios.record-meeting"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: Self.kind, provider: StillProvider()) { _ in
            RecordMeetingWidgetView()
        }
        .configurationDisplayName("Record a Meeting")
        .description("Start recording a meeting in one tap. LocalBook writes it up on this phone when you stop.")
        .supportedFamilies([.systemSmall, .accessoryCircular, .accessoryRectangular, .accessoryInline])
    }
}

/// The widget never changes, so it has one entry and never asks to be refreshed.
struct StillProvider: TimelineProvider {
    struct Entry: TimelineEntry { let date: Date }
    func placeholder(in context: Context) -> Entry { Entry(date: .now) }
    func getSnapshot(in context: Context, completion: @escaping (Entry) -> Void) { completion(Entry(date: .now)) }
    func getTimeline(in context: Context, completion: @escaping (Timeline<Entry>) -> Void) {
        completion(Timeline(entries: [Entry(date: .now)], policy: .never))
    }
}

struct RecordMeetingWidgetView: View {
    @Environment(\.widgetFamily) private var family

    var body: some View {
        content
            .widgetURL(RecorderLaunch.url)
            .containerBackground(.background, for: .widget)
            .accessibilityElement(children: .combine)
            .accessibilityLabel("Record a meeting with LocalBook")
    }

    @ViewBuilder private var content: some View {
        switch family {
        case .accessoryCircular:
            ZStack {
                AccessoryWidgetBackground()
                Image(systemName: "waveform").font(.title3.weight(.semibold))
            }
        case .accessoryRectangular:
            HStack(spacing: 8) {
                Image(systemName: "waveform").font(.title3.weight(.semibold))
                VStack(alignment: .leading, spacing: 0) {
                    Text("Record").font(.headline)
                    Text("a meeting").font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
            }
        case .accessoryInline:
            Label("Record a meeting", systemImage: "waveform")
        default:
            VStack(alignment: .leading, spacing: 0) {
                ZStack {
                    Circle().fill(.red.opacity(0.14))
                    Image(systemName: "waveform").font(.title2.weight(.semibold)).foregroundStyle(.red)
                }
                .frame(width: 48, height: 48)
                Spacer(minLength: 8)
                Text("Record a meeting").font(.headline).lineLimit(2).minimumScaleFactor(0.8)
                Text("Starts at once").font(.caption).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
            .widgetAccentable()
        }
    }
}

// MARK: Control Center, Lock Screen buttons, Action button

struct RecordMeetingControl: ControlWidget {
    static let kind = "com.patsteph.localbook.ios.record-meeting-control"

    var body: some ControlWidgetConfiguration {
        StaticControlConfiguration(kind: Self.kind) {
            ControlWidgetButton(action: RecordMeetingIntent()) {
                Label("Record Meeting", systemImage: "waveform")
            }
        }
        .displayName("Record Meeting")
        .description("Opens LocalBook and starts recording a meeting.")
    }
}
