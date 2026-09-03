import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons

// The parent's settings, in a window of their own. Opened from the panel
// after the PIN unlock; every change goes to the daemon as a partial patch
// and applies immediately.
Item {
  id: root

  property var service: null
  property string clientPath: ""
  property string pin: ""
  property string note: ""
  property color noteColor: Color.foreground

  readonly property bool lightTheme: {
    var bg = Color.background
    return (0.299 * bg.r + 0.587 * bg.g + 0.114 * bg.b) > 0.5
  }
  readonly property color okColor: lightTheme ? "#3C7C4E" : "#5FA46B"
  readonly property color errColor: lightTheme ? "#B03434" : "#E06C6C"

  readonly property bool divisionOn: service && service.earnOps
    && service.earnOps.indexOf("div") >= 0
  readonly property var tables: service && service.earnTables ? service.earnTables : []
  readonly property bool together: service ? service.philosophy === "together" : false

  // The list is held locally while the window is open, for the same reason
  // the number fields are: the daemon streams a fresh array every second, and
  // a live model would rebuild the rows under whoever is typing in one.
  property var localPeriods: []
  readonly property int periodLimit: 8
  readonly property string iconClose: "\uf00d"

  readonly property var dayKeys: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
  readonly property var dayLabels: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

  function fadeText(amount) {
    var c = Color.foreground
    var bg = Color.background
    return Qt.rgba(c.r + (bg.r - c.r) * amount,
                   c.g + (bg.g - c.g) * amount,
                   c.b + (bg.b - c.b) * amount, 1)
  }

  function show(pinValue) {
    pin = pinValue
    note = ""
    syncFields()
    win.visible = true
  }

  function close() {
    win.visible = false
    pin = ""
  }

  // Number and time fields are authoritative while the window is open, so
  // they are filled once on show instead of fighting the stream mid-edit.
  function syncFields() {
    if (!service) return
    for (var i = 0; i < dayKeys.length; i++) {
      var field = dayRepeater.itemAt(i)
      if (field) field.value = Number(service.budgetMinutes[dayKeys[i]]) || 0
    }
    localPeriods = clonePeriods()
    rewardField.value = service.earnSecondsPerCorrect
    capField.value = service.earnCapMinutes
    agreementField.text = String(service.agreementText || "")
    agreementMinutesField.value = service.agreementMinutes
    nudgeField.value = service.breakNudgeMinutes
  }

  function clonePeriods() {
    var out = []
    var source = service && service.blockedPeriods ? service.blockedPeriods : []
    for (var i = 0; i < source.length; i++) {
      out.push({ label: String(source[i].label || ""),
                 enabled: source[i].enabled === true,
                 start: String(source[i].start || ""),
                 end: String(source[i].end || "") })
    }
    return out
  }

  // Every write sends the whole array: the daemon merges dicts but replaces
  // lists, which is what you want here. A period that is gone is gone.
  function writePeriods(list) {
    localPeriods = list
    patch({ "blocked_periods": list })
  }

  function setPeriod(index, key, value) {
    var list = clonePeriodsFromLocal()
    if (index < 0 || index >= list.length) return
    if (list[index][key] === value) return
    list[index][key] = value
    writePeriods(list)
  }

  function clonePeriodsFromLocal() {
    var out = []
    for (var i = 0; i < localPeriods.length; i++) {
      out.push({ label: localPeriods[i].label, enabled: localPeriods[i].enabled,
                 start: localPeriods[i].start, end: localPeriods[i].end })
    }
    return out
  }

  function addPeriod() {
    var list = clonePeriodsFromLocal()
    if (list.length >= periodLimit) return
    list.push({ label: "New period", enabled: true, start: "18:00", end: "18:45" })
    writePeriods(list)
  }

  function removePeriod(index) {
    var list = clonePeriodsFromLocal()
    if (index < 0 || index >= list.length) return
    list.splice(index, 1)
    writePeriods(list)
  }

  function patch(obj) {
    if (patchProc.running) return
    patchProc.command = [root.clientPath, "--pin-stdin", "config", "patch", JSON.stringify(obj)]
    patchProc.running = true
  }

  function toggleTable(n) {
    var current = tables.slice()
    var index = current.indexOf(n)
    if (index >= 0) {
      if (current.length <= 1) return   // the last table stays
      current.splice(index, 1)
    } else {
      current.push(n)
    }
    patch({ "earn": { "tables": current } })
  }

  function validTime(text) {
    return /^([01]?\d|2[0-3]):[0-5]\d$/.test(text)
  }

  Process {
    id: patchProc
    stdinEnabled: true
    onStarted: write(root.pin + "\n")
    stdout: StdioCollector {
      onStreamFinished: {
        var payload
        try { payload = JSON.parse(text) } catch (e) { return }
        if (payload.ok === true) {
          root.note = "saved"
          root.noteColor = root.okColor
        } else if (payload.error === "bad_pin" || payload.error === "pin_locked_out") {
          root.note = "The PIN is not accepted any more. Close this window and unlock again."
          root.noteColor = root.errColor
        } else {
          root.note = String(payload.error || "failed")
          root.noteColor = root.errColor
        }
      }
    }
  }

  FloatingWindow {
    id: win
    visible: false
    title: "Screen Time settings"
    color: Color.background

    // Sized to the content, and fixed: a non-resizable window is floated by
    // Hyprland as a dialog instead of tiled over the screen, and a height
    // that follows the content leaves no dead space at the bottom.
    // Agreement mode hides the budget grid, the periods and the earning
    // knobs, so the same 520 leaves a lot of empty card next to a few short
    // controls. It gets its own, narrower measure.
    readonly property int fittedW: root.together ? 460 : 520
    readonly property int fittedH: Math.ceil(content.implicitHeight) + Style.space(40)
    implicitWidth: fittedW
    implicitHeight: fittedH
    minimumSize: Qt.size(fittedW, fittedH)
    maximumSize: Qt.size(fittedW, fittedH)

    FocusScope {
      anchors.fill: parent
      focus: true

      Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Escape) { root.close(); event.accepted = true }
      }

      Column {
        id: content
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: Style.space(20)
        spacing: Style.space(14)

        // --- approach -------------------------------------------------
        PanelSectionHeader {
          text: "Approach"
        }

        Row {
          spacing: Style.space(6)

          Button {
            text: "Limits"
            bordered: true
            selected: !root.together
            focusable: true
            onClicked: if (root.together) root.patch({ "philosophy": "limits" })
          }
          Button {
            text: "Agreement"
            bordered: true
            selected: root.together
            focusable: true
            onClicked: if (!root.together) root.patch({ "philosophy": "together" })
          }
        }

        Text {
          textFormat: Text.PlainText
          text: root.together
            ? "No lock and no rewards. An agreement in your own words, gentle reminders, and notes that stay the child's own."
            : "A daily budget, fair warnings, and a lock at zero. Extra minutes can be earned with math problems."
          width: parent.width
          wrapMode: Text.WordWrap
          color: root.fadeText(0.5)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }

        PanelSeparator { width: parent.width }

        // --- together: the agreement ----------------------------------
        Column {
          width: parent.width
          spacing: Style.space(10)
          visible: root.together

          PanelSectionHeader {
            text: "The agreement, in your own words (write it together)"
          }

          // An agreement is a few sentences, not a field, so it wraps and
          // grows. There is no editingFinished on a text area, so it saves
          // when the focus leaves and on ctrl+enter, and plain enter is a
          // newline like anywhere else you write prose.
          Rectangle {
            width: parent.width
            implicitHeight: Math.max(Style.space(84), agreementField.implicitHeight + Style.space(4))
            radius: Style.cornerRadius
            color: Style.controlFill(agreementField.activeFocus, agreementField.hovered,
                                     Color.foreground, Color.accent)
            border.width: 1
            border.color: agreementField.activeFocus
              ? Color.accent : root.fadeText(0.75)

            ScrollView {
              anchors.fill: parent
              anchors.margins: Style.space(2)
              clip: true

              TextArea {
                id: agreementField
                wrapMode: TextArea.Wrap
                activeFocusOnTab: true
                placeholderText: "On school days about an hour, and we stop before dinner."
                placeholderTextColor: root.fadeText(0.55)
                color: Color.foreground
                font.family: Style.font.family
                font.pixelSize: Style.font.body
                background: null

                function save() {
                  var value = text.trim()
                  if (value !== String(root.service ? root.service.agreementText : ""))
                    root.patch({ "agreement_text": value })
                }

                onActiveFocusChanged: if (!activeFocus) save()

                Keys.onPressed: function(event) {
                  if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                      && (event.modifiers & Qt.ControlModifier)) {
                    save(); event.accepted = true
                  } else if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) {
                    // A text area swallows tab as a character, which would
                    // take this field out of the keyboard's reach.
                    save()
                    nextItemInFocusChain(event.key === Qt.Key_Tab).forceActiveFocus()
                    event.accepted = true
                  }
                }
              }
            }
          }

          Row {
            spacing: Style.space(16)
            NumberField {
              id: agreementMinutesField
              label: "Agreed minutes (0 = none)"
              from: 0
              to: 1440
              stepSize: 5
              onModified: function(value) { root.patch({ "agreement_minutes": value }) }
            }
            NumberField {
              id: nudgeField
              label: "Break nudge (minutes, 0 = off)"
              from: 0
              to: 480
              stepSize: 5
              onModified: function(value) { root.patch({ "break_nudge_minutes": value }) }
            }
          }
        }

        // --- budget ---------------------------------------------------
        PanelSectionHeader {
          text: "Minutes per day"
          visible: !root.together
        }

        Row {
          visible: !root.together
          spacing: Style.space(8)
          Repeater {
            id: dayRepeater
            model: root.dayKeys.length
            delegate: NumberField {
              label: root.dayLabels[index]
              from: 0
              to: 1440
              stepSize: 5
              fieldWidth: Style.space(52)
              onModified: function(value) {
                var change = {}
                change[root.dayKeys[index]] = value
                root.patch({ "budget_minutes": change })
              }
            }
          }
        }

        PanelSeparator { width: parent.width; visible: !root.together }

        // --- blocked periods ------------------------------------------
        // One window was too rigid: a day can need school hours, dinner and
        // bedtime, so this is a list and bedtime is simply the first entry.
        Column {
          id: periodsColumn
          width: parent.width
          spacing: Style.space(12)
          visible: !root.together

          PanelSectionHeader {
            text: "BLOCKED PERIODS"
            foreground: Color.foreground
          }

          Repeater {
            model: root.localPeriods

            delegate: Column {
              id: periodRow
              required property var modelData
              required property int index
              width: periodsColumn.width
              spacing: Style.space(6)

              Row {
                id: periodTop
                width: parent.width
                spacing: Style.space(8)

                ToggleSwitch {
                  id: periodToggle
                  anchors.verticalCenter: parent.verticalCenter
                  checked: periodRow.modelData.enabled === true
                  onToggled: root.setPeriod(periodRow.index, "enabled",
                                            !(periodRow.modelData.enabled === true))
                }

                TextField {
                  width: periodTop.width - periodToggle.width - periodRemove.width
                         - periodTop.spacing * 2
                  text: String(periodRow.modelData.label || "")
                  placeholderText: "what this period is"
                  activeFocusOnTab: true
                  onEditingFinished: root.setPeriod(periodRow.index, "label", text.trim())
                }

                PanelActionButton {
                  id: periodRemove
                  iconText: root.iconClose
                  tooltipText: "Remove this period"
                  foreground: Color.foreground
                  hoverColor: root.errColor
                  size: Style.space(22)
                  focusable: true
                  anchors.verticalCenter: parent.verticalCenter
                  onClicked: root.removePeriod(periodRow.index)
                }
              }

              Row {
                spacing: Style.space(8)

                Text {
                  textFormat: Text.PlainText
                  text: "from"
                  color: root.fadeText(0.4)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                  anchors.verticalCenter: parent.verticalCenter
                }
                TextField {
                  width: Style.space(64)
                  text: String(periodRow.modelData.start || "")
                  activeFocusOnTab: true
                  onEditingFinished: if (root.validTime(text)) root.setPeriod(periodRow.index, "start", text)
                }
                Text {
                  textFormat: Text.PlainText
                  text: "until"
                  color: root.fadeText(0.4)
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                  anchors.verticalCenter: parent.verticalCenter
                }
                TextField {
                  width: Style.space(64)
                  text: String(periodRow.modelData.end || "")
                  activeFocusOnTab: true
                  onEditingFinished: if (root.validTime(text)) root.setPeriod(periodRow.index, "end", text)
                }
              }
            }
          }

          Row {
            spacing: Style.space(8)

            Button {
              text: "Add a period"
              focusable: true
              enabled: root.localPeriods.length < root.periodLimit
              onClicked: root.addPeriod()
            }

            Text {
              textFormat: Text.PlainText
              visible: root.localPeriods.length === 0
              text: "nothing is blocked yet"
              color: root.fadeText(0.5)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              anchors.verticalCenter: parent.verticalCenter
            }
          }
        }

        PanelSeparator { width: parent.width; visible: !root.together }

        // --- earning --------------------------------------------------
        Row {
          spacing: Style.space(10)
          visible: !root.together
          Text {
            textFormat: Text.PlainText
            text: "Earn minutes with math problems"
            color: Color.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.body
            anchors.verticalCenter: parent.verticalCenter
          }
          ToggleSwitch {
            anchors.verticalCenter: parent.verticalCenter
            checked: root.service ? root.service.earnEnabled === true : false
            onToggled: root.patch({ "earn": { "enabled": !(root.service && root.service.earnEnabled === true) } })
          }
        }

        Column {
          width: parent.width
          spacing: Style.space(10)
          visible: !root.together && (root.service ? root.service.earnEnabled === true : false)

          Row {
            spacing: Style.space(10)
            Text {
              textFormat: Text.PlainText
              text: "Division problems too"
              color: Color.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.body
              anchors.verticalCenter: parent.verticalCenter
            }
            ToggleSwitch {
              anchors.verticalCenter: parent.verticalCenter
              checked: root.divisionOn
              onToggled: root.patch({ "earn": { "ops": root.divisionOn ? ["mul"] : ["mul", "div"] } })
            }
          }

          PanelSectionHeader {
            text: "Multiplication tables"
          }

          Row {
            spacing: Style.space(4)
            Repeater {
              model: 10
              delegate: Button {
                text: String(index + 1)
                bordered: true
                selected: root.tables.indexOf(index + 1) >= 0
                focusable: true
                onClicked: root.toggleTable(index + 1)
              }
            }
          }

          Row {
            spacing: Style.space(16)
            NumberField {
              id: rewardField
              label: "Seconds per correct answer"
              from: 5
              to: 600
              stepSize: 5
              onModified: function(value) { root.patch({ "earn": { "seconds_per_correct": value } }) }
            }
            NumberField {
              id: capField
              label: "Max earned per day (minutes)"
              from: 0
              to: 480
              stepSize: 5
              onModified: function(value) { root.patch({ "earn": { "daily_cap_minutes": value } }) }
            }
          }
        }

        PanelSeparator { width: parent.width }

        Row {
          width: parent.width

          Text {
            textFormat: Text.PlainText
            text: root.note !== "" ? root.note : "Changes apply immediately."
            color: root.note !== "" ? root.noteColor : root.fadeText(0.5)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            width: parent.width - pinHint.width
            elide: Text.ElideRight
          }

          Text {
            id: pinHint
            textFormat: Text.PlainText
            text: "change the PIN: omarchy-screen-time pin set"
            color: root.fadeText(0.6)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
        }
      }
    }
  }
}
