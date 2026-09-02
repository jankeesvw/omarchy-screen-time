import QtQuick
import Quickshell
import qs.Ui
import qs.Commons

// The pill in the bar: a glyph for the phase and the time that is left today.
BarWidget {
  id: root
  moduleName: "jankeesvw.screentime"

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
  // towards the background instead of darkening, and pick the warning colours
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

  readonly property string label: blockedPhase ? (phase === "bedtime" ? "bedtime" : "0:00") : fmt(remaining)

  function plain(s) { return String(s || "").replace(/[<>]/g, "") }

  readonly property string tooltip: {
    if (!service || !connected) return ""
    var parts = [plain(service.profileName) + ": " + fmt(remaining) + " left of " + fmt(service.budgetSeconds)]
    if (service.earnedSeconds > 0) parts.push("earned " + fmt(service.earnedSeconds))
    if (service.grantedSeconds !== 0) parts.push("granted " + Math.round(service.grantedSeconds / 60) + "m")
    if (service.earnEnabled && service.earnRoomSeconds > 0)
      parts.push(fmt(service.earnRoomSeconds) + " earnable with math problems")
    if (phase === "bedtime") parts = ["It's bedtime."].concat(parts)
    return parts.join("  ·  ")
  }

  visible: connected
  implicitWidth: connected ? row.implicitWidth + Style.space(14) : 0
  implicitHeight: barSize

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
    anchors.fill: parent
    hoverEnabled: true
    onEntered: if (root.bar) root.bar.showTooltip(root, root.tooltip)
    onExited: if (root.bar) root.bar.hideTooltip(root)
  }
}
