import QtQuick
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons

// The pill in the bar plus the panel behind it. The pill shows a glyph for
// the phase and the time that is left; the panel is where the child earns
// minutes with math problems and where a parent, behind the PIN, hands out
// time with fixed choices.
Panel {
  id: root
  moduleName: "jankeesvw.screen-time"
  ipcTarget: "jankeesvw.screen-time"

  readonly property var service: bar && bar.shell ? bar.shell.serviceFor("jankeesvw.screen-time") : null
  readonly property bool connected: service ? service.connected === true : false
  readonly property string phase: service ? String(service.phase) : ""
  readonly property int remaining: service ? service.remainingSeconds : 0
  // In together mode the widget is a mirror, not a meter: it shows time
  // spent, never counts down, and carries no warning colours.
  readonly property bool together: service ? service.philosophy === "together" : false
  readonly property bool blockedPhase: phase === "empty" || phase === "bedtime"
  readonly property bool low: connected && !together && !blockedPhase
    && remaining <= (service ? service.minWarnSeconds : 60)

  // Glyphs as \u escapes so they survive the trip through the editor.
  readonly property string iconHourglass: "\uf254"
  readonly property string iconClock: "\uf017"
  readonly property string iconPause: "\uf04c"
  readonly property string iconLock: "\uf023"
  readonly property string iconUnlock: "\uf09c"
  readonly property string iconMoon: "\uf186"
  readonly property string iconGear: "\uf013"
  readonly property string iconClose: "\uf00d"

  readonly property string icon: {
    if (phase === "bedtime") return iconMoon
    if (phase === "empty") return iconLock
    if (phase === "paused") return iconPause
    if (phase === "idle") return iconClock
    return iconHourglass
  }

  // Colours that carry meaning have to hold up on light themes too, so blend
  // towards the background instead of darkening, and pick the accent colours
  // per theme.
  function fade(c, amount) {
    var bg = Color.background
    return Qt.rgba(c.r + (bg.r - c.r) * amount,
                   c.g + (bg.g - c.g) * amount,
                   c.b + (bg.b - c.b) * amount, 1)
  }
  readonly property bool lightTheme: {
    var bg = Color.background
    return (0.299 * bg.r + 0.587 * bg.g + 0.114 * bg.b) > 0.5
  }
  readonly property color warnColor: lightTheme ? "#B4620A" : "#E5A050"
  readonly property color blockColor: lightTheme ? "#B03434" : "#E06C6C"
  readonly property color okColor: lightTheme ? "#3C7C4E" : "#5FA46B"
  readonly property color pillColor: {
    if (!root.bar) return "white"
    if (blockedPhase) return blockColor
    if (low) return warnColor
    if (phase === "idle" || phase === "paused") return fade(root.bar.barForeground, 0.45)
    return root.bar.barForeground
  }

  function fmt(seconds) {
    seconds = Math.max(0, Math.floor(seconds))
    if (seconds >= 3600) {
      var h = Math.floor(seconds / 3600)
      var m = Math.floor((seconds % 3600) / 60)
      // A whole hour is "1h", not "1h00". The zeroes only earn their place
      // when there are minutes to read next to them.
      if (m === 0) return h + "h"
      return h + "h" + (m < 10 ? "0" : "") + m
    }
    if (seconds >= 600) return Math.floor(seconds / 60) + "m"
    var mm = Math.floor(seconds / 60)
    var ss = seconds % 60
    return mm + ":" + (ss < 10 ? "0" : "") + ss
  }

  function plain(s) { return String(s || "").replace(/[<>]/g, "") }

  // The wall clock time of a ledger entry, for the notes. Seconds since the
  // epoch, and anything that is not a number gets no label rather than a
  // wrong one.
  function clockTime(t) {
    var seconds = Number(t)
    if (!isFinite(seconds) || seconds <= 0) return ""
    var when = new Date(seconds * 1000)
    return Qt.formatTime(when, "HH:mm")
  }

  readonly property string label: {
    if (together) return fmt(service ? service.spentSeconds : 0)
    if (blockedPhase) return phase === "bedtime" ? "bedtime" : "0:00"
    return fmt(remaining)
  }

  readonly property string clientPath:
    Qt.resolvedUrl("bin/omarchy-screen-time").toString().replace(/^file:\/\//, "")

  // --- quiz state -----------------------------------------------------

  property var question: null        // {id, text, reward_seconds}
  property string quizNote: ""       // why there is no question (cap reached, ...)
  // Newest first, so the reward that just landed is always in view.
  readonly property var earnEventsView: service && service.earnEvents
    ? service.earnEvents.slice().reverse() : []
  property string feedback: ""
  property color feedbackColor: root.bar ? root.bar.barForeground : "white"
  readonly property bool earnEnabled: service ? service.earnEnabled === true : false

  // --- what the head of the panel says --------------------------------

  // One line under the name: what the clock is doing. The numbers moved to
  // the stat row, so this says the thing a number cannot.
  readonly property string stateLine: {
    if (!service) return ""
    if (phase === "empty") return "time is up"
    if (phase === "bedtime") return "bedtime"
    if (phase === "paused") return "paused by a parent"
    if (phase === "idle") return "idle, not counting"
    return "counting down"
  }

  // A fresh config names the profile "Default", which is nobody, and that is
  // what ends up in the biggest text on the card. Until a family gives a
  // child a name of their own, the panel says what it is instead.
  readonly property string heroTitle: {
    if (!service) return "Screen time"
    var name = plain(service.profileName).trim()
    return (name === "" || name === "Default") ? "Screen time" : name
  }

  // The line under the bar: what the bar itself cannot say. In limits mode
  // that is when the day ends, in agreement mode what the family settled on.
  // It lives here rather than in the hero's meta because the hero shouts its
  // meta in capitals, and a sentence is not a label.
  readonly property string underBarLine: {
    if (!service) return ""
    if (together) {
      if (service.agreementMinutes > 0)
        return "about " + fmt(service.agreementMinutes * 60) + " agreed"
      return ""
    }
    var bed = service.bedtime
    if (!bed || bed.enabled !== true || !bed.start) return ""
    return "bedtime at " + plain(String(bed.start))
  }

  // The day in even cells, so the eye can compare them instead of reading a
  // sentence. Earned and given only appear once they have something to say,
  // which keeps a plain day a plain two-cell row.
  readonly property var dayStats: {
    if (!service || together) return []
    var out = [{ label: "used", value: fmt(service.spentSeconds), accent: false }]
    if (service.earnedSeconds > 0)
      out.push({ label: "earned", value: fmt(service.earnedSeconds), accent: true })
    if (service.grantedSeconds > 0)
      out.push({ label: "given", value: fmt(service.grantedSeconds), accent: true })
    out.push({ label: "budget", value: fmt(service.budgetSeconds), accent: false })
    return out
  }

  function fetchQuestion() {
    if (quizProc.running) return
    quizProc.running = true
  }

  function submitAnswer() {
    if (!question || answerProc.running) return
    var given = answerField.text.trim()
    if (given === "") return
    answerProc.command = [root.clientPath, "answer", String(question.id), given]
    answerProc.running = true
  }

  function applyQuiz(payload) {
    if (payload.ok === true && payload.question) {
      question = payload.question
      quizNote = ""
      return
    }
    question = null
    if (payload.error === "daily_cap_reached") quizNote = "The bonus for today is full. Tomorrow there is room again."
    else if (payload.error === "earning_disabled") quizNote = ""
    else quizNote = "No question right now."
  }

  function applyVerdict(payload) {
    if (payload.error === "too_fast") {
      feedback = "Too fast, read it again. Try in " + payload.wait_seconds + "s."
      feedbackColor = root.warnColor
      return
    }
    if (payload.ok !== true) {
      feedback = payload.error === "expired" ? "That one expired, here is a new one." : "Something went wrong, new question."
      feedbackColor = root.warnColor
      answerField.text = ""
      fetchQuestion()
      return
    }
    if (payload.correct === true) {
      feedback = "Correct! +" + payload.reward_seconds + "s"
      feedbackColor = root.okColor
    } else {
      feedback = "Not quite: " + payload.text + " = " + payload.answer
      feedbackColor = root.blockColor
    }
    answerField.text = ""
    fetchQuestion()
  }

  // --- parent state ---------------------------------------------------

  property string parentPin: ""
  property bool parentUnlocked: false
  // The first PIN, held only between the click and the daemon reading it
  // off stdin. It never becomes a command line argument.
  property string pendingNewPin: ""
  // The daemon reports pin_set on its own schedule, so the panel remembers
  // that it just set one rather than showing the empty state again for a
  // tick.
  property bool pinJustSet: false
  // No stored PIN means no gate at all, so the drawer says so and offers to
  // fix it instead of pretending to be locked.
  readonly property bool pinMissing: connected && service !== null
    && service.pinSet !== true && !pinJustSet

  function setPin() {
    if (pinSetProc.running) return
    var pin = newPinField.text.trim()
    var again = newPinAgainField.text.trim()
    if (pin === "" || again === "") {
      newPinField.forceActiveFocus()
      return
    }
    if (pin !== again) {
      parentNote = "The two entries do not match."
      parentNoteColor = blockColor
      return
    }
    if (!/^[0-9]{4,12}$/.test(pin)) {
      parentNote = "A PIN is 4 to 12 digits."
      parentNoteColor = blockColor
      return
    }
    parentNote = ""
    pendingNewPin = pin
    pinSetProc.running = true
  }
  property string parentNote: ""
  property color parentNoteColor: root.bar ? root.bar.barForeground : "white"
  // Read off the note's own colour rather than tracked separately, so every
  // place that reports a refusal turns the drawer red without remembering to.
  readonly property bool parentError: parentNote !== ""
    && Qt.colorEqual(parentNoteColor, root.blockColor)

  // Takes the PIN rather than reading one field, because two drawers ask for
  // it now: the parent controls in limits mode and revisiting the agreement.
  function tryUnlock(pin) {
    if (unlockProc.running) return
    pin = String(pin || "").trim()
    if (pin === "") return
    root.parentPin = pin
    unlockProc.running = true
  }

  // The drawer's colour says where you are before you read a word. In
  // agreement mode a missing PIN is not something to flag: there is nothing
  // to gate there, so the drawer stays quiet instead of warning.
  function drawerColor(typed, flagMissingPin) {
    if (parentError) return fade(blockColor, 0.84)
    if (pinMissing) {
      if (!flagMissingPin) return fade(Color.popups.text, 0.9)
      return typed > 0 ? fade(Color.accent, Math.max(0.7, 0.88 - 0.045 * typed))
                       : fade(warnColor, 0.88)
    }
    if (parentUnlocked) return fade(okColor, 0.84)
    if (typed > 0) return fade(Color.accent, Math.max(0.7, 0.88 - 0.045 * typed))
    return fade(Color.popups.text, 0.9)
  }

  function grant(minutes) {
    if (actionProc.running) return
    actionProc.command = [root.clientPath, "--pin-stdin", "grant", String(minutes)]
    actionProc.pendingLabel = (minutes > 0 ? "+" : "") + minutes + " minutes"
    actionProc.running = true
  }

  function togglePause() {
    if (actionProc.running) return
    var cmd = root.phase === "paused" ? "resume" : "pause"
    actionProc.command = [root.clientPath, "--pin-stdin", cmd]
    actionProc.pendingLabel = cmd === "pause" ? "paused" : "resumed"
    actionProc.running = true
  }

  function resetPanel() {
    parentPin = ""
    parentUnlocked = false
    parentNote = ""
    feedback = ""
    pendingNewPin = ""
    pinJustSet = false
    pinField.text = ""
    answerField.text = ""
    newPinField.text = ""
    newPinAgainField.text = ""
  }

  onOpenedChanged: {
    if (opened) {
      if (earnEnabled && !together) fetchQuestion()
    } else {
      resetPanel()
    }
  }

  function forgetNote(t) {
    if (forgetProc.running) return
    var stamp = Number(t)
    if (!isFinite(stamp) || stamp <= 0) return
    forgetProc.command = [root.clientPath, "forget", String(stamp)]
    forgetProc.running = true
  }

  function submitReflection() {
    if (reflectProc.running) return
    var text = reflectField.text.trim()
    if (text === "") return
    reflectProc.command = [root.clientPath, "reflect", text]
    reflectProc.running = true
  }

  // --- processes ------------------------------------------------------

  Process {
    id: quizProc
    command: [root.clientPath, "quiz"]
    stdout: StdioCollector {
      onStreamFinished: {
        var payload
        try { payload = JSON.parse(text) } catch (e) { return }
        root.applyQuiz(payload)
      }
    }
  }

  Process {
    id: answerProc
    stdout: StdioCollector {
      onStreamFinished: {
        var payload
        try { payload = JSON.parse(text) } catch (e) { return }
        root.applyVerdict(payload)
      }
    }
  }

  Process {
    id: reflectProc
    stdout: StdioCollector {
      onStreamFinished: {
        var payload
        try { payload = JSON.parse(text) } catch (e) { return }
        if (payload.ok === true) reflectField.text = ""
      }
    }
  }

  Process {
    id: forgetProc
    // The list redraws off the watch stream, so nothing to do here but read
    // the answer and let a refusal be quiet: a note that would not go is
    // still on screen, which is the whole message.
    stdout: StdioCollector {
      onStreamFinished: {
        var payload
        try { payload = JSON.parse(text) } catch (e) { return }
      }
    }
  }

  Process {
    id: pinSetProc
    // The new PIN goes in over stdin, never as an argument: every argument
    // of every process is readable by anybody on the machine.
    command: [root.clientPath, "pin", "set"]
    stdinEnabled: true
    onStarted: write(root.pendingNewPin + "\n")
    stdout: StdioCollector {
      onStreamFinished: {
        var payload
        try { payload = JSON.parse(text) } catch (e) { payload = null }
        root.pendingNewPin = ""
        newPinField.text = ""
        newPinAgainField.text = ""
        if (payload && payload.ok === true) {
          root.pinJustSet = true
          root.parentNote = "PIN set. The drawer is locked from now on."
          root.parentNoteColor = root.okColor
          pinField.forceActiveFocus()
        } else {
          root.parentNote = payload && payload.error === "pin_must_be_4_to_12_digits"
            ? "A PIN is 4 to 12 digits." : "Could not set the PIN."
          root.parentNoteColor = root.blockColor
        }
      }
    }
  }

  Process {
    id: unlockProc
    command: [root.clientPath, "--pin-stdin", "config", "get"]
    stdinEnabled: true
    onStarted: write(root.parentPin + "\n")
    stdout: StdioCollector {
      onStreamFinished: {
        var payload
        try { payload = JSON.parse(text) } catch (e) { return }
        if (payload.ok === true) {
          root.parentUnlocked = true
          root.parentNote = ""
          pinField.text = ""
        } else if (payload.error === "pin_locked_out") {
          root.parentNote = "Too many tries. Wait " + payload.retry_in_seconds + "s."
          root.parentNoteColor = root.blockColor
        } else {
          root.parentNote = "That is not the PIN."
          root.parentNoteColor = root.blockColor
        }
      }
    }
  }

  Process {
    id: actionProc
    property string pendingLabel: ""
    stdinEnabled: true
    onStarted: write(root.parentPin + "\n")
    stdout: StdioCollector {
      onStreamFinished: {
        var payload
        try { payload = JSON.parse(text) } catch (e) { return }
        if (payload.ok === true) {
          // The demo answers ok to every write and changes nothing, so say
          // that rather than reporting minutes that were never handed out.
          root.parentNote = payload.demo === true
            ? "Demo mode, nothing changed." : actionProc.pendingLabel
          root.parentNoteColor = payload.demo === true
            ? root.fade(Color.popups.text, 0.3) : root.okColor
        } else if (payload.error === "pin_locked_out") {
          root.parentUnlocked = false
          root.parentNote = "Too many tries. Wait " + payload.retry_in_seconds + "s."
          root.parentNoteColor = root.blockColor
        } else if (payload.error === "bad_pin") {
          root.parentUnlocked = false
          root.parentNote = "The PIN changed. Enter it again."
          root.parentNoteColor = root.blockColor
        } else {
          root.parentNote = String(payload.error || "failed")
          root.parentNoteColor = root.blockColor
        }
      }
    }
  }

  SettingsWindow {
    id: settingsWindow
    service: root.service
    clientPath: root.clientPath
  }

  // --- the pill -------------------------------------------------------

  visible: connected
  implicitWidth: connected ? row.implicitWidth + Style.space(14) : 0
  implicitHeight: bar ? bar.barSize : Style.bar.sizeHorizontal

  Row {
    id: row
    anchors.centerIn: parent
    spacing: Style.space(6)

    Text {
      textFormat: Text.PlainText
      anchors.verticalCenter: parent.verticalCenter
      text: root.icon
      color: root.pillColor
      font.family: root.bar ? root.bar.fontFamily : Style.font.family
      font.pixelSize: Style.font.caption
      Behavior on color {
        enabled: !root.bar || root.bar.foregroundAnimationEnabled
        ColorAnimation { duration: 160 }
      }
    }

    Text {
      textFormat: Text.PlainText
      anchors.verticalCenter: parent.verticalCenter
      visible: !root.bar || !root.bar.vertical
      text: root.label
      color: root.pillColor
      font.family: root.bar ? root.bar.fontFamily : Style.font.family
      font.pixelSize: Style.font.body
      Behavior on color {
        enabled: !root.bar || root.bar.foregroundAnimationEnabled
        ColorAnimation { duration: 160 }
      }
    }
  }

  MouseArea {
    id: pillArea
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: Qt.PointingHandCursor
    onClicked: root.toggle()
  }

  // --- the panel ------------------------------------------------------

  KeyboardPanel {
    id: panel
    anchorItem: root
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: root.together ? reflectField
      : (root.earnEnabled ? answerField : (root.pinMissing ? newPinField : pinField))
    // Agreement mode is nearly all prose, and prose reads better on a
    // narrower measure than a card full of controls.
    readonly property int desiredWidth: Style.space(root.together ? 280 : 330)
    contentWidth: Math.min(desiredWidth,
                           panel.availableCardWidth > 0 ? panel.availableCardWidth : desiredWidth)
    contentHeight: panel.fittedContentHeight(content.implicitHeight)

    // A form, so a plain Item as the key catcher: Tab walks the controls the
    // way Qt already knows how to, and only the panel-wide keys live here.
    Item {
      id: keyCatcher
      anchors.fill: parent
      focus: true

      Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Escape) { root.close(); event.accepted = true }
      }

      Column {
        id: content
        width: parent.width
        // The shell's own rhythm: 14 between sections, 6 inside one.
        spacing: Style.space(14)

        // header, in the shell's own hero shape: icon, name, the time as the
        // detail pill, and one uppercase meta line underneath
        PanelHero {
          width: parent.width
          foreground: Color.popups.text
          title: root.heroTitle
          detail: {
            if (root.together) return root.fmt(root.service ? root.service.spentSeconds : 0) + " today"
            if (root.blockedPhase) return root.phase === "bedtime" ? "bedtime" : "time's up"
            return root.fmt(root.remaining) + " left"
          }
          // One line, so one fact at a time: the hero meta elides rather than
          // wraps. Earned and granted minutes already show further down, and
          // the agreed time is also the progress bar's scale.
          meta: {
            if (!root.service) return ""
            if (root.together) {
              if (root.phase === "paused") return "paused"
              if (root.service.stretchSeconds >= 600)
                return root.fmt(root.service.stretchSeconds) + " without a break"
              return ""
            }
            return root.stateLine
          }
          iconComponent: Component {
            Text {
              id: heroIcon
              textFormat: Text.PlainText
              text: root.icon
              color: root.pillColor
              font.family: Style.font.family
              font.pixelSize: Style.font.display

              // An hourglass that never turns is a drawing. Turning it over
              // every twenty seconds is the panel saying the day is still
              // running, which is exactly when the glyph is an hourglass:
              // paused, idle and bedtime all draw something else.
              property real flip: 0
              rotation: flip

              Behavior on flip {
                NumberAnimation { duration: 700; easing.type: Easing.InOutCubic }
              }

              Timer {
                interval: 20000
                repeat: true
                running: root.opened && root.phase === "running"
                onTriggered: heroIcon.flip += 180
              }
            }
          }
        }

        // The day, broken into cells of exactly equal width so the figures
        // line up under each other however many there are. The cell is a
        // plain Item and the labels are centred inside it: a nested layout
        // sizes itself to its content and packs everything to the left.
        Row {
          id: statRow
          width: parent.width
          spacing: 0
          visible: root.dayStats.length > 0

          Repeater {
            model: root.dayStats

            delegate: Item {
              width: statRow.width / Math.max(1, root.dayStats.length)
              height: statCell.implicitHeight

              Column {
                id: statCell
                anchors.centerIn: parent
                spacing: Style.space(4)

                Text {
                  textFormat: Text.PlainText
                  text: modelData.value
                  anchors.horizontalCenter: parent.horizontalCenter
                  color: modelData.accent === true ? root.okColor : Color.popups.text
                  font.family: Style.font.family
                  font.pixelSize: Style.font.title
                  font.bold: true
                }

                Text {
                  textFormat: Text.PlainText
                  text: String(modelData.label).toUpperCase()
                  anchors.horizontalCenter: parent.horizontalCenter
                  color: root.fade(Color.popups.text, 0.45)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  font.bold: true
                  font.letterSpacing: 1.2
                }
              }
            }
          }
        }

        // How far into the day you are: spent versus everything there is
        // today (or versus the agreement, in together mode, where the bar
        // stays a neutral colour whatever it says). The bar and the line
        // under it are one block, so they sit closer than the sections do.
        Column {
          width: parent.width
          spacing: Style.space(6)

          // Square ends, because the hatching runs to the edge and a rounded
          // cap would cut the diagonals off mid stroke.
          Rectangle {
            id: dayBar
            width: parent.width
            height: Style.space(12)
            radius: 0
            visible: !root.together || (root.service && root.service.agreementMinutes > 0)
            color: root.fade(Color.popups.text, 0.86)

            readonly property int total: {
              if (!root.service) return 0
              if (root.together) return root.service.agreementMinutes * 60
              return root.service.spentSeconds + root.remaining
            }
            readonly property real fraction: total > 0
              ? Math.min(1, (root.service ? root.service.spentSeconds : 0) / total) : 0
            readonly property color fillColor: root.together
              ? (root.bar ? root.bar.barForeground : "white") : root.pillColor

            // The hatching: thin bars on the diagonal, one pitch apart. The
            // pitch is also how far the pattern has to travel before it repeats,
            // so drifting by exactly one pitch loops without a seam.
            readonly property int stroke: Style.space(2)
            readonly property int pitch: Style.space(7)
            property real drift: 0

            NumberAnimation on drift {
              from: 0
              to: dayBar.pitch
              duration: 2200
              loops: Animation.Infinite
              // Only while time is actually being used up, and only while
              // somebody is looking: a drifting pattern behind a closed panel
              // is work nobody asked for. Agreement mode drifts too. The rule
              // there is that nothing counts down and nothing warns, and a
              // pattern saying the clock runs does neither.
              running: root.opened && root.phase === "running"
            }

            Item {
              id: fill
              height: parent.height
              width: parent.width * dayBar.fraction

              Behavior on width {
                NumberAnimation { duration: 300; easing.type: Easing.OutCubic }
              }

              // The base tint under the hatching.
              Rectangle {
                anchors.fill: parent
                color: root.fade(dayBar.fillColor, 0.55)
              }

              Item {
                anchors.fill: parent
                clip: true

                Row {
                  height: parent.height
                  x: -dayBar.pitch + dayBar.drift
                  spacing: dayBar.pitch - dayBar.stroke

                  Repeater {
                    // Counted off the track, not off the fill, so the model
                    // does not churn while the fill animates.
                    model: Math.ceil((dayBar.width + dayBar.pitch * 4) / dayBar.pitch)

                    delegate: Rectangle {
                      width: dayBar.stroke
                      // Taller than the bar and lifted, so the 45 degree turn
                      // still covers the full height at both ends.
                      height: dayBar.height * 2.4
                      y: -dayBar.height * 0.7
                      color: dayBar.fillColor
                      rotation: 45
                      antialiasing: true
                    }
                  }
                }
              }
            }
          }

          // When the day ends, which the bar cannot say because bedtime is not
          // a share of the budget.
          Text {
            textFormat: Text.PlainText
            visible: root.underBarLine !== ""
            text: root.underBarLine
            width: parent.width
            color: root.fade(Color.popups.text, 0.45)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }

        }

        // --- together mode: the agreement and the child's own notes ------
        PanelSeparator { width: parent.width; visible: root.together }

        Column {
          width: parent.width
          spacing: Style.space(6)
          visible: root.together

          PanelSectionHeader {
            text: "OUR AGREEMENT"
            foreground: Color.popups.text
          }

          Text {
            textFormat: Text.PlainText
            text: root.service && root.service.agreementText !== ""
              ? "“" + root.plain(root.service.agreementText) + "”"
              : "Nothing written down yet. Make one together."
            width: parent.width
            wrapMode: Text.WordWrap
            font.italic: root.service ? root.service.agreementText !== "" : false
            color: Color.popups.text
            font.family: Style.font.family
            font.pixelSize: Style.font.body
          }

        }

        PanelSeparator { width: parent.width; visible: root.together }

        Column {
          width: parent.width
          spacing: Style.space(6)
          visible: root.together

          PanelSectionHeader {
            text: "HOW IS IT GOING?"
            foreground: Color.popups.text
          }

          // No button next to it: enter keeps the note, and the placeholder
          // is where that is said, now that the button is gone. Rounded like
          // the bubbles underneath, because it is the one you are writing.
          TextField {
            id: reflectField
            width: parent.width
            placeholderText: "a note to yourself, enter keeps it"
            activeFocusOnTab: true
            Keys.onPressed: function(event) {
              if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                root.submitReflection(); event.accepted = true
              } else if (event.key === Qt.Key_Escape) {
                root.close(); event.accepted = true
              }
            }

            // Only the corner radius differs from the shared field, so the
            // fill and the border spec come from the field itself.
            background: BorderSurface {
              color: Style.controlFill(reflectField._focused, reflectField._hot,
                                       reflectField.foreground, reflectField.accent)
              borderSpec: reflectField._borderSpec
              radius: Math.max(Style.cornerRadius, Style.space(10))
            }
          }

          // The notes read as a conversation with yourself, so they are
          // bubbles and they run oldest to newest, the way a chat does. The
          // rounding is deliberate rather than the theme's: a square bubble
          // is not a bubble, so it takes the larger of the two.
          Column {
            id: notes
            width: parent.width
            // Bubbles need room to read as separate notes rather than as one
            // block of text, so they sit further apart than a list row would.
            // The top padding is the gap to the field you write them in.
            spacing: Style.space(10)
            topPadding: Style.space(12)
            bottomPadding: Style.space(4)

            Repeater {
              model: root.service && root.service.reflections
                ? root.service.reflections : []

              // Anchored rather than a Row: the times line up in one column
              // on the right instead of trailing each bubble at whatever
              // width it happens to have, which reads as ragged.
              delegate: Item {
                id: noteRow
                width: notes.width
                height: bubble.height

                required property var modelData
                required property int index
                // Every other note leans the other way. A degree is enough to
                // read as handwriting on a wall; more and it reads as broken.
                readonly property real tilt: (index % 2 === 0 ? -0.9 : 0.8)
                // The hover covers the whole row, not just the bubble. With it
                // on the bubble the cross faded out exactly as the pointer
                // travelled to it, and an invisible button still takes clicks,
                // so it could also be hit blind.
                readonly property bool showForget: rowHover.hovered || forgetButton.activeFocus

                HoverHandler { id: rowHover }

                Rectangle {
                  id: bubble
                  anchors.left: parent.left
                  anchors.top: parent.top
                  radius: Math.max(Style.cornerRadius, Style.space(10))
                  color: root.fade(Color.popups.text, 0.86)
                  width: bubbleText.width + Style.space(20)
                  height: bubbleText.implicitHeight + Style.space(16)
                  rotation: noteRow.tilt
                  antialiasing: true

                  Text {
                    id: bubbleText
                    textFormat: Text.PlainText
                    text: root.plain(noteRow.modelData.text)
                    // Clamped against its own natural width, so a short note
                    // gets a short bubble and a long one wraps at the card.
                    width: Math.min(implicitWidth, notes.width * 0.72)
                    wrapMode: Text.WordWrap
                    anchors.centerIn: parent
                    color: Color.popups.text
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                  }
                }

                // Taking a note back. Hidden until the pointer is on the
                // bubble, but it also appears on keyboard focus, because a
                // control that only a mouse can find is not a control.
                PanelActionButton {
                  id: forgetButton
                  iconText: root.iconClose
                  tooltipText: "Forget this note"
                  foreground: Color.popups.text
                  hoverColor: root.blockColor
                  size: Style.space(20)
                  focusable: true
                  opacity: noteRow.showForget ? 1 : 0
                  anchors.right: noteTime.left
                  anchors.rightMargin: Style.space(6)
                  anchors.verticalCenter: bubble.verticalCenter
                  onClicked: root.forgetNote(noteRow.modelData.t)

                  Behavior on opacity {
                    NumberAnimation { duration: 120 }
                  }
                }

                Text {
                  id: noteTime
                  textFormat: Text.PlainText
                  text: root.clockTime(noteRow.modelData.t)
                  visible: text !== ""
                  anchors.right: parent.right
                  anchors.bottom: bubble.bottom
                  anchors.bottomMargin: Style.space(5)
                  color: root.fade(Color.popups.text, 0.55)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                }
              }
            }
          }

        }

        // Revisiting the agreement lives at the very bottom, under the
        // notes: it is the one thing here a parent reaches for, and it
        // should not sit between the child and their own words.
        PanelSeparator { width: parent.width; visible: root.together }

        // The same two steps as the parent drawer in limits mode: the PIN
        // first, the button after. Where no PIN exists there is nothing to
        // unlock, and agreement mode is allowed to work without one, so the
        // button simply stands on its own.
        Rectangle {
          width: parent.width
          // It used to inherit this from the section it sat in; standing on
          // its own it has to say so itself, or it turns up in limits mode.
          visible: root.together
          radius: Style.cornerRadius
          implicitHeight: togetherContent.implicitHeight + Style.space(12)
          color: root.drawerColor(togetherPinField.text.length, false)

          Behavior on color {
            ColorAnimation { duration: 180; easing.type: Easing.OutCubic }
          }

          Item {
            id: togetherContent
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: Style.space(10)
            anchors.rightMargin: Style.space(10)
            anchors.verticalCenter: parent.verticalCenter
            implicitHeight: root.parentUnlocked || root.pinMissing
              ? revisitRow.implicitHeight : togetherLockRow.implicitHeight
            height: implicitHeight

            Row {
              id: togetherLockRow
              width: parent.width
              spacing: Style.space(8)
              visible: !root.parentUnlocked && !root.pinMissing

              TextField {
                id: togetherPinField
                width: Math.max(Style.space(80),
                                togetherLockRow.width - togetherUnlock.implicitWidth
                                  - togetherLockRow.spacing)
                password: true
                placeholderText: "PIN"
                activeFocusOnTab: true
                inputMethodHints: Qt.ImhDigitsOnly
                Keys.onPressed: function(event) {
                  if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                    root.tryUnlock(togetherPinField.text); event.accepted = true
                  } else if (event.key === Qt.Key_Escape) {
                    root.close(); event.accepted = true
                  }
                }
              }

              Button {
                id: togetherUnlock
                text: "Unlock"
                focusable: true
                anchors.verticalCenter: parent.verticalCenter
                onClicked: root.tryUnlock(togetherPinField.text)
              }
            }

            Row {
              id: revisitRow
              width: parent.width
              spacing: Style.space(8)
              visible: root.parentUnlocked || root.pinMissing

              Button {
                text: "Revisit together"
                focusable: true
                onClicked: {
                  settingsWindow.show(root.parentPin)
                  root.close()
                }
              }
            }
          }
        }

        Text {
          textFormat: Text.PlainText
          visible: root.together && root.parentNote !== ""
          text: root.parentNote
          width: parent.width
          wrapMode: Text.WordWrap
          color: root.parentNoteColor
          font.family: Style.font.family
          font.pixelSize: Style.font.body
        }

        // --- limits mode: earning and the parent controls ----------------
        PanelSeparator { width: parent.width; visible: root.earnEnabled && !root.together }

        // earn minutes with math problems
        Column {
          width: parent.width
          spacing: Style.space(6)
          visible: root.earnEnabled && !root.together

          PanelSectionHeader {
            text: "EARN MINUTES"
            foreground: Color.popups.text
          }

          // The one thing the child came here to do, so it sits on a surface
          // of its own rather than reading as another line of text. The
          // answer field takes what is left over, which puts Check against
          // the right edge whatever the sum is.
          Rectangle {
            width: parent.width
            visible: root.question !== null
            radius: Style.cornerRadius
            color: root.fade(Color.popups.text, 0.88)
            implicitHeight: questionRow.implicitHeight + Style.space(12)

            Row {
              id: questionRow
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.leftMargin: Style.space(10)
              anchors.rightMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(10)

              Text {
                id: questionText
                textFormat: Text.PlainText
                text: root.question ? String(root.question.text) + " =" : ""
                color: Color.popups.text
                font.family: Style.font.family
                font.bold: true
                font.pixelSize: Style.font.title
                anchors.verticalCenter: parent.verticalCenter
              }

              TextField {
                id: answerField
                width: Math.max(Style.space(52),
                                questionRow.width - questionText.width
                                  - checkButton.implicitWidth - questionRow.spacing * 2)
                activeFocusOnTab: true
                inputMethodHints: Qt.ImhFormattedNumbersOnly
                Keys.onPressed: function(event) {
                  if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                    root.submitAnswer(); event.accepted = true
                  } else if (event.key === Qt.Key_Escape) {
                    root.close(); event.accepted = true
                  }
                }
              }

              Button {
                id: checkButton
                text: "Check"
                focusable: true
                anchors.verticalCenter: parent.verticalCenter
                onClicked: root.submitAnswer()
              }
            }
          }

          Text {
            textFormat: Text.PlainText
            visible: root.quizNote !== ""
            text: root.quizNote
            width: parent.width
            wrapMode: Text.WordWrap
            color: root.fade(Color.popups.text, 0.3)
            font.family: Style.font.family
            font.pixelSize: Style.font.body
          }

          Text {
            textFormat: Text.PlainText
            visible: root.feedback !== ""
            text: root.feedback
            color: root.feedbackColor
            font.family: Style.font.family
            font.pixelSize: Style.font.body
          }

          Text {
            textFormat: Text.PlainText
            visible: root.question !== null && root.service !== null
            text: root.question && root.service
              ? "+" + root.question.reward_seconds + "s per correct answer  ·  " + root.fmt(root.service.earnRoomSeconds) + " earnable today"
              : ""
            color: root.fade(Color.popups.text, 0.45)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }

          // The tally: every reward earned today, newest on top. The list
          // scrolls inside a capped height, so the card never outgrows the
          // screen however good the day was.
          Column {
            width: parent.width
            spacing: Style.space(3)
            visible: root.earnEventsView.length > 0

            PanelSectionHeader {
              text: "SUMS TODAY  ·  " + root.fmt(root.service ? root.service.earnedSeconds : 0).toUpperCase() + " EARNED"
              foreground: Color.popups.text
            }

            ListView {
              id: earnList
              width: parent.width
              height: Math.min(contentHeight, Style.space(110))
              clip: true
              interactive: contentHeight > height
              model: root.earnEventsView
              delegate: Item {
                width: earnList.width
                height: earnRowText.implicitHeight + Style.space(10)

                readonly property bool missed: modelData.kind === "miss"

                // The sum is what happened, the right hand side is what it
                // was worth: the question recedes and the outcome carries the
                // colour. A miss keeps the answer that was given, struck
                // through, so the afternoon shows the hard tables too.
                Text {
                  id: earnRowText
                  textFormat: Text.PlainText
                  text: root.plain(modelData.q)
                  color: root.fade(Color.popups.text, 0.2)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                  anchors.verticalCenter: parent.verticalCenter
                }
                Row {
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  spacing: Style.space(6)

                  Text {
                    textFormat: Text.PlainText
                    visible: parent.parent.missed
                    text: String(modelData.given)
                    color: root.blockColor
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                    font.strikeout: true
                  }

                  Text {
                    textFormat: Text.PlainText
                    text: parent.parent.missed
                      ? String(modelData.answer)
                      : "+" + Number(modelData.seconds) + "s"
                    color: parent.parent.missed
                      ? root.fade(Color.popups.text, 0.35) : root.okColor
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                    font.bold: !parent.parent.missed
                  }
                }
              }
            }
          }
        }

        PanelSeparator { width: parent.width; visible: !root.together }

        // parent controls
        Column {
          width: parent.width
          spacing: Style.space(6)
          visible: !root.together

          // The header carries the lock itself, so the state is readable
          // from the shape before anybody reads the words.
          Item {
            width: parent.width
            implicitHeight: parentHeader.implicitHeight

            PanelSectionHeader {
              id: parentHeader
              text: "PARENT"
              foreground: Color.popups.text
            }

            Text {
              textFormat: Text.PlainText
              // An open lock for a drawer that has no PIN either, because
              // that is exactly what it is: not shut.
              text: root.parentUnlocked || root.pinMissing ? root.iconUnlock : root.iconLock
              color: root.pinMissing ? root.warnColor
                : (root.parentUnlocked ? root.okColor : root.fade(Color.popups.text, 0.5))
              anchors.right: parent.right
              anchors.verticalCenter: parentHeader.verticalCenter
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }

          // The drawer. Quiet while it is shut, warming towards the accent
          // as the PIN comes in, green once it is open and red when the PIN
          // was wrong: the colour says where you are before the note does.
          Rectangle {
            id: parentDrawer
            width: parent.width
            radius: Style.cornerRadius
            implicitHeight: parentContent.implicitHeight + Style.space(12)

            color: root.drawerColor(root.pinMissing ? newPinField.text.length
                                                    : pinField.text.length, true)

            Behavior on color {
              ColorAnimation { duration: 180; easing.type: Easing.OutCubic }
            }

            Item {
              id: parentContent
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.leftMargin: Style.space(10)
              anchors.rightMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter
              implicitHeight: root.pinMissing ? pinSetup.implicitHeight
                : (root.parentUnlocked ? actionFlow.implicitHeight : lockRow.implicitHeight)
              height: implicitHeight

              // Nothing is locked yet. Rather than a box that opens on any
              // input, the drawer says so and takes the first PIN here.
              Column {
                id: pinSetup
                width: parent.width
                spacing: Style.space(8)
                visible: root.pinMissing

                Text {
                  textFormat: Text.PlainText
                  text: "No PIN yet, so anyone can hand out minutes. Pick one and this drawer is yours."
                  width: parent.width
                  wrapMode: Text.WordWrap
                  color: root.fade(Color.popups.text, 0.2)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                }

                TextField {
                  id: newPinField
                  width: parent.width
                  password: true
                  placeholderText: "new PIN, 4 to 12 digits"
                  activeFocusOnTab: true
                  inputMethodHints: Qt.ImhDigitsOnly
                  Keys.onPressed: function(event) {
                    if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                      newPinAgainField.forceActiveFocus(); event.accepted = true
                    } else if (event.key === Qt.Key_Escape) {
                      root.close(); event.accepted = true
                    }
                  }
                }

                Row {
                  width: parent.width
                  spacing: Style.space(8)

                  TextField {
                    id: newPinAgainField
                    width: Math.max(Style.space(80),
                                    parent.width - setPinButton.implicitWidth - parent.spacing)
                    password: true
                    placeholderText: "once more"
                    activeFocusOnTab: true
                    inputMethodHints: Qt.ImhDigitsOnly
                    Keys.onPressed: function(event) {
                      if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                        root.setPin(); event.accepted = true
                      } else if (event.key === Qt.Key_Escape) {
                        root.close(); event.accepted = true
                      }
                    }
                  }

                  Button {
                    id: setPinButton
                    text: "Set PIN"
                    focusable: true
                    anchors.verticalCenter: parent.verticalCenter
                    onClicked: root.setPin()
                  }
                }
              }

              Row {
                id: lockRow
                width: parent.width
                spacing: Style.space(8)
                visible: !root.parentUnlocked && !root.pinMissing

                TextField {
                  id: pinField
                  width: Math.max(Style.space(80),
                                  lockRow.width - unlockButton.implicitWidth - lockRow.spacing)
                  password: true
                  placeholderText: "PIN"
                  activeFocusOnTab: true
                  inputMethodHints: Qt.ImhDigitsOnly
                  Keys.onPressed: function(event) {
                    if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                      root.tryUnlock(pinField.text); event.accepted = true
                    } else if (event.key === Qt.Key_Escape) {
                      root.close(); event.accepted = true
                    }
                  }
                }

                Button {
                  id: unlockButton
                  text: "Unlock"
                  focusable: true
                  anchors.verticalCenter: parent.verticalCenter
                  onClicked: root.tryUnlock(pinField.text)
                }
              }

              // A Flow, not a Row: with the pause and settings buttons this
              // no longer fits on one line inside the card, so it wraps
              // instead of running out of the border.
              Flow {
                id: actionFlow
                width: parent.width
                spacing: Style.space(6)
                visible: root.parentUnlocked && !root.pinMissing

                Button { text: "+15"; focusable: true; onClicked: root.grant(15) }
                Button { text: "+60"; focusable: true; onClicked: root.grant(60) }
                Button { text: "-15"; focusable: true; onClicked: root.grant(-15) }
                Button {
                  text: root.phase === "paused" ? "Resume" : "Pause"
                  focusable: true
                  onClicked: root.togglePause()
                }

                // The odd one out in this row: it opens a window instead of
                // handing out time, so it is a gear rather than a word. The
                // caption under the card names it for a keyboard, which never
                // gets to see a tooltip.
                PanelActionButton {
                  iconText: root.iconGear
                  tooltipText: "Settings"
                  foreground: Color.popups.text
                  size: Style.spacing.controlHeight
                  focusable: true
                  bordered: true
                  onClicked: {
                    settingsWindow.show(root.parentPin)
                    root.close()
                  }
                }
              }
            }
          }

          Text {
            textFormat: Text.PlainText
            visible: root.parentNote !== ""
            text: root.parentNote
            color: root.parentNoteColor
            font.family: Style.font.family
            font.pixelSize: Style.font.body
          }
        }

      }
    }
  }
}
