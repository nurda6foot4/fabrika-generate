/**
 * premiere_import.jsx — ExtendScript for Adobe Premiere Pro
 * Scans the fabrika-generate/output folder and imports all .mp4 clips
 * into a new Project bin named "Fabrika-YYYY-MM-DD".
 *
 * How to run:
 *   File > Scripts > Run Script File...  → select this file
 *   OR from terminal:
 *   osascript -e 'tell application "Adobe Premiere Pro 2024" to do script "/path/to/premiere_import.jsx"'
 *
 * Compatible with: Premiere Pro CS6, CC, 2019–2025 (ExtendScript ES3)
 */

(function () {

    // ── Config ──────────────────────────────────────────────────────────────

    // Absolute path to the fabrika-generate output folder.
    // Update this if you move the project.
    var OUTPUT_FOLDER = "/Users/nurdauletzhumaliyev/fabrika-generate/output";

    // ── Helpers ─────────────────────────────────────────────────────────────

    function padTwo(n) {
        return n < 10 ? "0" + n : "" + n;
    }

    function todayString() {
        var d = new Date();
        return d.getFullYear() + "-" + padTwo(d.getMonth() + 1) + "-" + padTwo(d.getDate());
    }

    function collectMp4s(folder) {
        var results = [];
        var items = folder.getFiles();
        for (var i = 0; i < items.length; i++) {
            var item = items[i];
            if (item instanceof Folder) {
                // recurse into job sub-folders
                var sub = collectMp4s(item);
                for (var j = 0; j < sub.length; j++) {
                    results.push(sub[j]);
                }
            } else if (item instanceof File) {
                var name = item.name.toLowerCase();
                if (name.substr(name.length - 4) === ".mp4") {
                    results.push(item);
                }
            }
        }
        return results;
    }

    // ── Main ────────────────────────────────────────────────────────────────

    var proj = app.project;
    if (!proj) {
        alert("No project is open in Premiere Pro. Please open a project first.");
        return;
    }

    var outputFolder = new Folder(OUTPUT_FOLDER);
    if (!outputFolder.exists) {
        alert("Output folder not found:\n" + OUTPUT_FOLDER +
              "\n\nRun the pipeline first to generate clips.");
        return;
    }

    var mp4Files = collectMp4s(outputFolder);
    if (mp4Files.length === 0) {
        alert("No .mp4 files found in:\n" + OUTPUT_FOLDER +
              "\n\nRun the pipeline (python3 main.py ...) first.");
        return;
    }

    // Create a new bin for this import batch
    var binName = "Fabrika-" + todayString();
    var root = proj.rootItem;

    // Check if bin already exists (re-import safety)
    var targetBin = null;
    for (var k = 0; k < root.children.numItems; k++) {
        if (root.children[k].name === binName) {
            targetBin = root.children[k];
            break;
        }
    }
    if (!targetBin) {
        targetBin = root.createBin(binName);
    }

    // Import all files into the bin
    var paths = [];
    for (var m = 0; m < mp4Files.length; m++) {
        paths.push(mp4Files[m].fsName);
    }

    // importFiles(paths, suppressUI, targetBin, importAsNumberedStills)
    proj.importFiles(paths, true, targetBin, false);

    // Sort bin items by name for clean sequence ordering
    targetBin.children.sort();

    alert(
        "✅ Fabrika Import Complete\n\n" +
        "Bin: " + binName + "\n" +
        "Clips imported: " + paths.length + "\n\n" +
        "Tip: Select all clips in the bin, right-click →\n" +
        "\"New Sequence From Clip\" to auto-build a sequence."
    );

})();
