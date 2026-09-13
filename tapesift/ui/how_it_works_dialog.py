"""How TapeSift Works: the app explaining itself, in one place.

Written as plain English rather than a feature list, because the questions
people actually have are "what do I do first" and "why did it do that" -
not "what buttons exist". Each section answers one of those.

The text lives here rather than in a docs file so it ships inside the app
and cannot be lost or left behind by an installer.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QListWidget, QListWidgetItem,
    QTextBrowser, QVBoxLayout,
)

SECTIONS: list[tuple[str, str]] = [
    ("The short version", """
<h3>The short version</h3>
<p>TapeSift turns a long game film into named, searchable clips.</p>
<ol>
<li><b>Start a project</b> and point it at one film.</li>
<li><b>Detect Plays (Beta)</b> finds the plays for you, or cut them by hand with
<b>I</b> and <b>O</b>.</li>
<li><b>Log each play</b> - who, what down, what happened.</li>
<li><b>Search everything</b> later in the Library, across every game you
have ever broken down.</li>
<li><b>Export</b> single clips or a combined reel.</li>
</ol>
<p>Editing, playback and play detection run locally. Optional First Read sends
selected frame contact sheets to your configured provider only when you run it.</p>
"""),
    ("Playing and scrubbing", """
<h3>Playing and scrubbing</h3>
<p>These keys control the video when playback has focus. Text fields, pickers
and dialogs keep their editing keys. Press Esc to return to playback.</p>
<ul>
<li><b>Space</b> play / pause.</li>
<li><b>J / K / L</b> reverse, stop, forward. Tap J or L again to speed up
(1x, 2x, 4x, 8x).</li>
<li><b>Left / Right</b> step one frame when paused; hold to run.</li>
<li><b>Mouse wheel over the timeline</b> jumps; hold <b>Shift</b> to step
frame by frame.</li>
<li><b>Ctrl + mouse wheel</b> zooms around the pointer. When zoomed, drag
the slim bar beneath the timeline to move across the game without seeking;
<b>Show Full Timeline (Ctrl+0)</b> returns to the full-film view.</li>
</ul>
<p><b>Why the first scrub can feel slower.</b> Game film uses a keyframe
every few seconds, so jumping backwards makes the decoder rebuild a lot of
frames. TapeSift builds a small 720p <i>preview copy</i> in the background
with a keyframe every 15 frames, and scrubs that instead - designed for faster seeking. Exports always cut from the original file, never the
preview.</p>
<p>Preview builds use processing time and disk space. Background work is paused
during scrubbing to reduce competition with playback. Configure automatic
previews in Settings &gt; Playback.</p>
"""),
    ("Cutting clips by hand", """
<h3>Cutting clips by hand</h3>
<ul>
<li><b>I</b> marks the start; <b>O</b> marks the end.</li>
<li>With the default <b>After the play</b> prompt, O opens the details form.
Fill in the form and save. The <b>At the snap</b> option opens it on I instead.</li>
<li>With prompts set to <b>Never</b>, mark with I/O, then press <b>A</b> to
open <b>Name This Clip</b>. Enter a name and confirm.</li>
<li>With prompts enabled, A reopens the details form for the marked range.</li>
<li>The <b>New Clip</b> menu also offers timestamp, range, bulk paste and CSV import.</li>
</ul>
<p>Choose the prompt behavior in <b>Settings &gt; Clip Defaults</b>.</p>
"""),
    ("Detect Plays (Beta)", """
<h3>Detect Plays (Beta)</h3>
<p><b>Beta feature for All-22 cut-up film only.</b> Broadcast footage, highlight reels and
sideline video will not work.</p>
<p>Cut-up film is assembled one play at a time, and the assembly leaves a
boundary between plays - a black gap, a scoreboard card, or a hard cut.
TapeSift finds those boundaries and makes a clip per play, with the in-point
already sitting before the snap.</p>
<p>It then measures the film to learn what a play looks like <i>in that
film</i>: how many camera angles each play is shown from, and how long a
normal play runs. Films from different producers differ, so fixed numbers
would be wrong for half of them.</p>
<p><b>Sections marked REVIEW.</b> Anything TapeSift cannot confidently call
a play is still handed to you, marked for review, with the reason. It is
never thrown away. Usually it is two plays whose separator was missed -
split it and carry on.</p>
<p><b>Coverage Review.</b> After detection, TapeSift also lists every source
range that did not become a clip. <b>CHECK FIRST</b> means the range has
play-like duration or separator structure; <b>LOW SIGNAL</b> means the
structural match is weaker, not that it is safe to skip. Inspect it, create an
exact review clip, or mark it Not a Play. Decisions are reversible and never
change the original video or captured detector result.</p>
<p>If TapeSift warns that camera changes look unreliable, it has found what
appears to be one very long camera angle, which usually means two angles it
cannot tell apart. It will stop suggesting split points rather than cut
plays in half.</p>
<p><b>Test a short section, not a whole game.</b> Set In and Out in gaps
around a 10–15 minute range, then choose <b>Playback &gt; Detection diagnostics &gt; Start Autodetect
Test Batch</b>. Review every candidate in that range. Explicitly confirm false
positives, and mark any ordinary clip you add for a missed play. You must
watch every second between In and Out, including gaps where no detector clip
exists; <b>Playback &gt; Detection diagnostics &gt; Play Active Autodetect Test Range</b> replays that
continuous scope. Finish the batch to see scoped development precision,
recall, F1, and boundary error. Aim for at least 20 confirmed plays per batch.
Those numbers describe only the confirmed range, not the whole game.</p>
"""),
    ("Logging plays", """
<h3>Logging plays</h3>
<p>Each clip carries play details: quarter, down &amp; distance, ball on,
play type, run or pass, formations and personnel, result, the player
involved, and anyone else worth naming.</p>
<p>Use fixed dropdown lists for consistent terms, or learned values for
free typing with autocomplete. Choose the mode and edit the lists in
<b>Settings &gt; Clip Defaults</b>.</p>
<p><b>Auto-name from details</b> builds the clip name for you - for example
<i>Damon Wilson Sack - Q2 3rd &amp; 7 on 35</i> - and that name becomes the
exported filename. Details also become tags, which is what makes the
by-player and by-situation export folders work.</p>
<p><b>Review mode (F5)</b> walks the clip list one play at a time,
replaying each clip and putting the cursor in the details panel.
<b>Ctrl+Shift+Down</b> jumps to the next clip that still needs logging.</p>
"""),
    ("The Library", """
<h3>The Library</h3>
<p>Every clip you log is indexed into one catalog that spans all your
projects - so you can ask questions across a whole season, not just the
game that happens to be open.</p>
<p>Search by player, opponent, tag, play type, formation, result, or
anything in the clip name or notes. Different spellings of the same tag are
treated as one.</p>
<p>You do <b>not</b> need to export first - a clip is searchable as soon as
it is logged and the project is saved.</p>
<p>The catalog is a derived index. If it ever looks wrong or incomplete,
<b>Rebuild Index</b> reads your project files and builds it again. Nothing
lives only in the catalog, so it can always be rebuilt.</p>
<p>One thing worth knowing: projects are found by scanning your project
folder. A project saved somewhere else - an exports folder, say - may not
be picked up.</p>
"""),
    ("Exporting", """
<h3>Exporting</h3>
<p>Export individual clips, one combined reel, or both.</p>
<p><b>Quick Export (Ctrl+E)</b> immediately queues the selected clip using
its assigned preset and the project's saved cutting mode. Use export setup
to review options first.</p>
<ul>
<li><b>Source Quality</b> keeps the original resolution - the default.</li>
<li><b>Fast Copy</b> is fastest but cuts land on keyframes, so clips can
start slightly early or late.</li>
<li><b>Social 1080p</b> and <b>Vertical 9:16</b> reformat for posting.</li>
</ul>
<p>Filenames come from the clip name, so what you type while logging is
what lands on disk. Folder templates can sort exports automatically - for
example <i>{player}/{quarter}/{down_distance}</i>.</p>
<p>CPU encoding is the default. Select an available hardware encoder in
<b>Settings &gt; Export</b> to use it; unsupported or failed hardware encoding
falls back to the processor.</p>
"""),
    ("Your files", """
<h3>Your files</h3>
<p>Each project is a single <b>.tapesift</b> file - a document you can
move, copy, or back up like any other. Clips are saved continuously, and
the project reopens exactly as you left it.</p>
<p>TapeSift never copies or moves your film. It only reads it, so the
source video must stay on disk. If you move it, the top bar offers
<b>Relink Source</b> and your clip times are preserved.</p>
<p><b>File &gt; Rename Project</b> (F2) renames the project you are working
in without closing it, and keeps the Library pointing at the right file.</p>
<p>If the app ever closes unexpectedly, it offers to restore the project
next time it starts.</p>
"""),
]


class HowItWorksDialog(QDialog):
    """Sectioned explanation of the app, opened from the menu bar."""

    def __init__(self, parent=None, section: int = 0) -> None:
        super().__init__(parent)
        self.setWindowTitle("How TapeSift Works")
        self.setMinimumSize(760, 560)

        layout = QVBoxLayout(self)
        body = QHBoxLayout()
        body.setSpacing(14)

        self.contents = QListWidget()
        self.contents.setMaximumWidth(190)
        self.contents.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for title, _ in SECTIONS:
            self.contents.addItem(QListWidgetItem(title))
        self.contents.currentRowChanged.connect(self._show_section)
        body.addWidget(self.contents)

        self.text = QTextBrowser()
        self.text.setOpenExternalLinks(False)
        body.addWidget(self.text, 1)
        layout.addLayout(body, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.contents.setCurrentRow(max(0, min(section, len(SECTIONS) - 1)))

    def _show_section(self, row: int) -> None:
        if 0 <= row < len(SECTIONS):
            self.text.setHtml(SECTIONS[row][1])
            self.text.verticalScrollBar().setValue(0)
