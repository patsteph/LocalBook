import Foundation

/// About three minutes of plain prose, split into the chunks that are voiced one at a time (so
/// playback can start at once and a stall between chunks is measurable). No digits, symbols or
/// abbreviations: the transcript is compared word for word against this text.
enum SpeechCorpus {
    static let chunks: [String] = [
        "People have always kept notes. A farmer marks the days of the rain, a sailor writes down the tides, and a student copies out the lines that seem important. The habit is old because the problem is old: memory is generous at first and then it quietly lets go.",
        "A good note does more than record a fact. It leaves a trail back to the moment when the fact mattered. When you read it again months later, you should be able to recall who said it, why you were listening, and what you meant to do about it.",
        "That is why the best collections of notes are small and connected, not large and tidy. A few well chosen sentences, each placed beside the ideas it belongs with, will help you more than a thousand pages filed in perfect order and never opened again.",
        "Search changes how we use them. Once you can ask a question in your own words and receive the passages that answer it, the shape of the folder no longer matters. What matters is that the right words were captured, and that they were captured honestly.",
        "Privacy matters here too. Notes are some of the most personal writing a person produces. They hold half formed thoughts, names of friends, and worries that nobody else should read. A tool for keeping them should treat that trust as its first job.",
        "That means the work should happen close to the person. Reading a page, finding the meaning in it, and answering a question can all be done on the device in your hand, without sending a single word across the network to a stranger.",
        "Speech opens another door. Many of our best ideas arrive while walking, driving, or washing the dishes, when typing is awkward. A voice that records the thought, and a voice that reads an answer back, make notes useful in the hours that used to be lost.",
        "There is a cost, of course. Listening and speaking take energy, and a phone is a small machine that gets warm when it works hard. A careful design asks how long a task will run, how much memory it needs, and whether it can keep going when the screen goes dark.",
        "Those questions are better answered by measurement than by hope. So we read a long passage aloud, we transcribe the sound back into words, and we count the mistakes. If the machine hears what it just said, the whole chain is working.",
        "When the screen is locked and the phone rests on a table, the work should simply continue, quietly, until the last sentence has been spoken. If it stops, or stutters, or forgets where it was, we want to learn that now, before anyone depends on it.",
    ]
    static var fullText: String { chunks.joined(separator: " ") }
    static var wordCount: Int { fullText.split(whereSeparator: \.isWhitespace).count }
}
