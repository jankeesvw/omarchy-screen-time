import QtQuick
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
    startField.text = service.bedtime && service.bedtime.start ? String(service.bedtime.start) : "20:00"
    endField.text = service.bedtime && service.bedtime.end ? String(service.bedtime.end) : "07:00"
    rewardField.value = service.earnSecondsPerCorrect
    capField.value = service.earnCapMinutes
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
    title: "Screentime settings"
    color: Color.background
    implicitWidth: 520
    implicitHeight: 640
    // A fixed size makes the window non-resizable, and Hyprland floats a
    // non-resizable window as a dialog instead of tiling it over the screen.
    minimumSize: Qt.size(520, 640)
    maximumSize: Qt.size(520, 640)

    FocusScope {
      anchors.fill: parent
      focus: true

      Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Escape) { root.close(); event.accepted = true }
      }

      Column {
        id: content
        anchors.fill: parent
        anchors.margins: Style.space(20)
        spacing: Style.space(14)

        // --- budget ---------------------------------------------------
        Text {
          textFormat: Text.PlainText
          text: "Minutes per day"
          color: root.fadeText(0.4)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }

        Row {
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

        PanelSeparator { width: parent.width }

        // --- bedtime --------------------------------------------------
        Row {
          spacing: Style.space(10)
          Text {
            textFormat: Text.PlainText
            text: "Bedtime"
            color: Color.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.body
            anchors.verticalCenter: parent.verticalCenter
          }
          ToggleSwitch {
            anchors.verticalCenter: parent.verticalCenter
            checked: root.service && root.service.bedtime ? root.service.bedtime.enabled === true : false
            onToggled: root.patch({ "bedtime": { "enabled": !(root.service && root.service.bedtime && root.service.bedtime.enabled === true) } })
          }
        }

        Row {
          spacing: Style.space(8)
          visible: root.service && root.service.bedtime ? root.service.bedtime.enabled === true : false

          Text {
            textFormat: Text.PlainText
            text: "from"
            color: root.fadeText(0.4)
            font.family: Style.font.family
            font.pixelSize: Style.font.body
            anchors.verticalCenter: parent.verticalCenter
          }
          TextField {
            id: startField
            width: Style.space(64)
            activeFocusOnTab: true
            onEditingFinished: if (root.validTime(text)) root.patch({ "bedtime": { "start": text } })
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
            id: endField
            width: Style.space(64)
            activeFocusOnTab: true
            onEditingFinished: if (root.validTime(text)) root.patch({ "bedtime": { "end": text } })
          }
        }

        PanelSeparator { width: parent.width }

        // --- earning --------------------------------------------------
        Row {
          spacing: Style.space(10)
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
          visible: root.service ? root.service.earnEnabled === true : false

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

          Text {
            textFormat: Text.PlainText
            text: "Multiplication tables"
            color: root.fadeText(0.4)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
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

        Text {
          textFormat: Text.PlainText
          text: root.note !== "" ? root.note : "Changes apply immediately."
          color: root.note !== "" ? root.noteColor : root.fadeText(0.5)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }

        Text {
          textFormat: Text.PlainText
          text: "The PIN is changed on the command line: omarchy-screentime pin set (it asks for the current PIN first)."
          width: parent.width
          wrapMode: Text.WordWrap
          color: root.fadeText(0.5)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }
    }
  }
}
