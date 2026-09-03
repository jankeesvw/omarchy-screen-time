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
  moduleName: "jankeesvw.screentime"
  ipcTarget: "jankeesvw.screentime"

  readonly property var service: bar && bar.shell ? bar.shell.serviceFor("jankeesvw.screentime") : null
  readonly property bool connected: service ? service.connected === true : false
  readonly property string phase: service ? String(service.phase) : ""
  readonly property int remaining: service ? service.remainingSeconds : 0
  readonly property bool blockedPhase: phase === "empty" || phase === "bedtime"
  readonly property bool low: connected && !blockedPhase
    && remaining <= (service ? service.minWarnSeconds : 60)

  // Glyphs as \u escapes so they survive the trip through the editor.
  readonly property string iconHourglass: "\uf254"
  readonly property string iconClock: "\uf017"
  readonly property string iconPause: "\uf04c"
  readonly property string iconLock: "\uf023"
  readonly property string iconMoon: "\uf186"

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
      return h + "h" + (m < 10 ? "0" : "") + m
    }
    if (seconds >= 600) return Math.floor(seconds / 60) + "m"
    var mm = Math.floor(seconds / 60)
    var ss = seconds % 60
    return mm + ":" + (ss < 10 ? "0" : "") + ss
  }

  function plain(s) { return String(s || "").replace(/[<>]/g, "") }

  readonly property string label: blockedPhase ? (phase === "bedtime" ? "bedtime" : "0:00") : fmt(remaining)

  readonly property string clientPath:
    Qt.resolvedUrl("bin/omarchy-screentime").toString().replace(/^file:\/\//, "")

  // --- quiz state -----------------------------------------------------

  property var question: null        // {id, text, reward_seconds}
  property string quizNote: ""       // why there is no question (cap reached, ...)
  property string feedback: ""
  property color feedbackColor: root.bar ? root.bar.barForeground : "white"
  readonly property bool earnEnabled: service ? service.earnEnabled === true : false

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
  property string parentNote: ""
  property color parentNoteColor: root.bar ? root.bar.barForeground : "white"

  function tryUnlock() {
    if (unlockProc.running) return
    var pin = pinField.text.trim()
    if (pin === "") return
    root.parentPin = pin
    unlockProc.running = true
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
    pinField.text = ""
    answerField.text = ""
  }

  onOpenedChanged: {
    if (opened) {
      if (earnEnabled) fetchQuestion()
    } else {
      resetPanel()
    }
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
          root.parentNote = actionProc.pendingLabel
          root.parentNoteColor = root.okColor
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
    focusTarget: root.earnEnabled ? answerField : pinField
    contentWidth: Math.min(Style.space(330),
                           panel.availableCardWidth > 0 ? panel.availableCardWidth : Style.space(330))
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
        spacing: Style.space(10)

        // header
        Row {
          width: parent.width
          spacing: Style.space(8)

          Text {
            textFormat: Text.PlainText
            text: root.icon
            color: root.pillColor
            font.family: Style.font.family
            font.pixelSize: Style.font.title
            anchors.verticalCenter: parent.verticalCenter
          }
          Column {
            spacing: Style.space(1)
            Text {
              textFormat: Text.PlainText
              text: (root.service ? root.plain(root.service.profileName) : "") + ": " + root.fmt(root.remaining) + " left"
              color: Color.popups.text
              font.family: Style.font.family
              font.bold: true
              font.pixelSize: Style.font.body
            }
            Text {
              textFormat: Text.PlainText
              text: {
                if (!root.service) return ""
                var parts = [root.fmt(root.service.budgetSeconds) + " budget"]
                if (root.service.earnedSeconds > 0) parts.push(root.fmt(root.service.earnedSeconds) + " earned")
                if (root.service.grantedSeconds !== 0) parts.push(Math.round(root.service.grantedSeconds / 60) + "m granted")
                if (root.phase === "bedtime") parts = ["It's bedtime"].concat(parts)
                if (root.phase === "paused") parts = ["paused"].concat(parts)
                return parts.join("  ·  ")
              }
              color: root.fade(Color.popups.text, 0.45)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }
        }

        PanelSeparator { width: parent.width; visible: root.earnEnabled }

        // earn minutes with math problems
        Column {
          width: parent.width
          spacing: Style.space(6)
          visible: root.earnEnabled

          Text {
            textFormat: Text.PlainText
            text: "Earn minutes"
            color: root.fade(Color.popups.text, 0.45)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }

          Row {
            width: parent.width
            spacing: Style.space(10)
            visible: root.question !== null

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
              width: Style.space(70)
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
              text: "Check"
              focusable: true
              anchors.verticalCenter: parent.verticalCenter
              onClicked: root.submitAnswer()
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
        }

        PanelSeparator { width: parent.width }

        // parent controls
        Column {
          width: parent.width
          spacing: Style.space(6)

          Text {
            textFormat: Text.PlainText
            text: "Parent"
            color: root.fade(Color.popups.text, 0.45)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }

          Row {
            width: parent.width
            spacing: Style.space(8)
            visible: !root.parentUnlocked

            TextField {
              id: pinField
              width: Style.space(110)
              password: true
              placeholderText: "PIN"
              activeFocusOnTab: true
              inputMethodHints: Qt.ImhDigitsOnly
              Keys.onPressed: function(event) {
                if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                  root.tryUnlock(); event.accepted = true
                } else if (event.key === Qt.Key_Escape) {
                  root.close(); event.accepted = true
                }
              }
            }

            Button {
              text: "Unlock"
              focusable: true
              anchors.verticalCenter: parent.verticalCenter
              onClicked: root.tryUnlock()
            }
          }

          Row {
            spacing: Style.space(6)
            visible: root.parentUnlocked

            Button { text: "+15"; focusable: true; onClicked: root.grant(15) }
            Button { text: "+30"; focusable: true; onClicked: root.grant(30) }
            Button { text: "+60"; focusable: true; onClicked: root.grant(60) }
            Button { text: "-15"; focusable: true; onClicked: root.grant(-15) }
            Button {
              text: root.phase === "paused" ? "Resume" : "Pause"
              focusable: true
              onClicked: root.togglePause()
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

        Text {
          textFormat: Text.PlainText
          text: "enter confirms  ·  tab moves  ·  esc closes"
          color: root.fade(Color.popups.text, 0.55)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }
    }
  }
}
