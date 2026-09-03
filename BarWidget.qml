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
      if (earnEnabled && !together) fetchQuestion()
    } else {
      resetPanel()
    }
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
    focusTarget: root.together ? reflectField : (root.earnEnabled ? answerField : pinField)
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
        spacing: Style.space(10)

        // header, in the shell's own hero shape: icon, name, the time as the
        // detail pill, and one uppercase meta line underneath
        PanelHero {
          width: parent.width
          foreground: Color.popups.text
          title: root.service ? root.plain(root.service.profileName) : ""
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
            if (root.phase === "paused") return "paused"
            if (root.together) {
              if (root.service.stretchSeconds >= 600)
                return root.fmt(root.service.stretchSeconds) + " without a break"
              if (root.service.agreementMinutes > 0)
                return "about " + root.fmt(root.service.agreementMinutes * 60) + " agreed"
              return ""
            }
            return root.fmt(root.service.spentSeconds) + " used  ·  " + root.fmt(root.service.budgetSeconds) + " budget"
          }
          iconComponent: Component {
            Text {
              textFormat: Text.PlainText
              text: root.icon
              color: root.pillColor
              font.family: Style.font.family
              font.pixelSize: Style.font.display
            }
          }
        }

        // How far into the day you are: spent versus everything there is
        // today (or versus the agreement, in together mode, where the bar
        // stays a neutral colour whatever it says).
        Rectangle {
          width: parent.width
          height: Style.space(4)
          radius: height / 2
          visible: !root.together || (root.service && root.service.agreementMinutes > 0)
          color: root.fade(Color.popups.text, 0.85)

          Rectangle {
            readonly property int total: {
              if (!root.service) return 0
              if (root.together) return root.service.agreementMinutes * 60
              return root.service.spentSeconds + root.remaining
            }
            height: parent.height
            radius: parent.radius
            width: parent.width * (total > 0 ? Math.min(1, (root.service ? root.service.spentSeconds : 0) / total) : 0)
            color: root.together ? (root.bar ? root.bar.barForeground : "white") : root.pillColor

            Behavior on width {
              NumberAnimation { duration: 300; easing.type: Easing.OutCubic }
            }
          }
        }

        // --- together mode: the agreement and the child's own notes ------
        PanelSeparator { width: parent.width; visible: root.together }

        Column {
          width: parent.width
          spacing: Style.space(6)
          visible: root.together

          PanelSectionHeader {
            text: "Our agreement"
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

          Row {
            spacing: Style.space(8)

            TextField {
              id: togetherPinField
              width: Style.space(90)
              password: true
              placeholderText: "PIN"
              visible: root.service ? root.service.pinSet === true : false
              activeFocusOnTab: true
              inputMethodHints: Qt.ImhDigitsOnly
            }
            Button {
              text: "Revisit together"
              focusable: true
              anchors.verticalCenter: parent.verticalCenter
              onClicked: {
                settingsWindow.show(togetherPinField.text.trim())
                togetherPinField.text = ""
                root.close()
              }
            }
          }
        }

        PanelSeparator { width: parent.width; visible: root.together }

        Column {
          width: parent.width
          spacing: Style.space(6)
          visible: root.together

          PanelSectionHeader {
            text: "How is it going?"
            foreground: Color.popups.text
          }

          Row {
            width: parent.width
            spacing: Style.space(8)

            TextField {
              id: reflectField
              width: parent.width - reflectButton.implicitWidth - Style.space(8)
              placeholderText: "a note to yourself about today"
              activeFocusOnTab: true
              Keys.onPressed: function(event) {
                if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                  root.submitReflection(); event.accepted = true
                } else if (event.key === Qt.Key_Escape) {
                  root.close(); event.accepted = true
                }
              }
            }
            Button {
              id: reflectButton
              text: "Keep"
              focusable: true
              anchors.verticalCenter: parent.verticalCenter
              onClicked: root.submitReflection()
            }
          }

          Repeater {
            model: root.service && root.service.reflections
              ? root.service.reflections.slice().reverse() : []
            delegate: Text {
              textFormat: Text.PlainText
              text: root.plain(modelData.text)
              width: parent.width
              wrapMode: Text.WordWrap
              color: root.fade(Color.popups.text, 0.25)
              font.family: Style.font.family
              font.pixelSize: Style.font.body
            }
          }

          Text {
            textFormat: Text.PlainText
            text: "These notes are yours. Show them if you want to."
            width: parent.width
            wrapMode: Text.WordWrap
            color: root.fade(Color.popups.text, 0.55)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
        }

        // --- limits mode: earning and the parent controls ----------------
        PanelSeparator { width: parent.width; visible: root.earnEnabled && !root.together }

        // earn minutes with math problems
        Column {
          width: parent.width
          spacing: Style.space(6)
          visible: root.earnEnabled && !root.together

          PanelSectionHeader {
            text: "Earn minutes"
            foreground: Color.popups.text
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

          // The tally: every reward earned today, newest on top. The list
          // scrolls inside a capped height, so the card never outgrows the
          // screen however good the day was.
          Column {
            width: parent.width
            spacing: Style.space(3)
            visible: root.earnEventsView.length > 0

            PanelSectionHeader {
              text: "Earned today  ·  " + root.fmt(root.service ? root.service.earnedSeconds : 0)
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
                height: earnRowText.implicitHeight + Style.space(3)

                Text {
                  id: earnRowText
                  textFormat: Text.PlainText
                  text: root.plain(modelData.q)
                  color: Color.popups.text
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                  anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                  textFormat: Text.PlainText
                  text: "+" + Number(modelData.seconds) + "s"
                  color: root.okColor
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
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

          PanelSectionHeader {
            text: "Parent"
            foreground: Color.popups.text
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

          // A Flow, not a Row: with the pause and settings buttons this no
          // longer fits on one line inside the card, so it wraps instead of
          // running out of the border.
          Flow {
            width: parent.width
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
            Button {
              text: "Settings"
              focusable: true
              onClicked: {
                settingsWindow.show(root.parentPin)
                root.close()
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

        Text {
          textFormat: Text.PlainText
          text: "enter confirms  ·  tab moves  ·  esc closes"
          width: parent.width
          wrapMode: Text.WordWrap
          color: root.fade(Color.popups.text, 0.55)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }
    }
  }
}
