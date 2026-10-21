import SwiftUI

struct NotebooksView: View {
    let library: LibraryModel

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(library.notebooks) { n in
                        HStack {
                            Text(n.title)
                            Spacer()
                            Text("\(n.chunks) passages").font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                    if library.notebooks.isEmpty {
                        Text("Nothing here yet. Tap Sync to copy your library from your Mac.").foregroundStyle(.secondary)
                    }
                } footer: {
                    Text("Notebooks you switched to “Kept off phones” on the Mac never arrive.")
                }
                Section {
                    switch library.syncState {
                    case .idle:
                        Button("Sync now") { Task { await library.sync() } }
                    case .syncing(let line):
                        HStack { ProgressView(); Text(line).font(.footnote) }
                    case .failed(let why):
                        Text(why).font(.footnote).foregroundStyle(.red)
                        Button("Try again") { Task { await library.sync() } }
                    }
                    Text(library.lastSyncLine).font(.footnote).foregroundStyle(.secondary)
                } header: { Text("Sync") } footer: {
                    Text("Your Mac must be running LocalBook with Sync on, on the same network.")
                }
            }
            .refreshable { await library.sync() }
            .navigationTitle("Notebooks")
        }
    }
}
