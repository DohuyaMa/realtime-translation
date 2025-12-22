import QtQuick
import QtQuick.Layouts
import QtQuick.Controls
import org.kde.kirigami as Kirigami

Kirigami.Page {
    id: dashboardPage
    title: "Dashboard"

    actions: [
        Kirigami.Action {
            text: "Start Translation"
            icon.name: "media-playback-start"
            onTriggered: console.log("Start translation")
        },
        Kirigami.Action {
            text: "Stop Translation"
            icon.name: "media-playback-stop"
            onTriggered: console.log("Stop translation")
        }
    ]

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Kirigami.Units.gridUnit

        Kirigami.Heading {
            text: "Real-time Translator"
            level: 1
            Layout.alignment: Qt.AlignHCenter
        }

        Kirigami.Separator {
            Layout.fillWidth: true
            Layout.topMargin: Kirigami.Units.gridUnit
            Layout.bottomMargin: Kirigami.Units.gridUnit
        }

        // Language Settings
        Kirigami.Card {
            Layout.fillWidth: true
            Layout.preferredHeight: Kirigami.Units.gridUnit * 8

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Kirigami.Units.smallSpacing

                Kirigami.Heading {
                    text: "Language Settings"
                    level: 3
                }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        text: "Source Language:"
                    }
                    ComboBox {
                        model: ["Auto", "Ukrainian (uk)", "Polish (pl)"]
                        Layout.fillWidth: true
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        text: "Target Language:"
                    }
                    Label {
                        text: "English (en)"
                        Layout.fillWidth: true
                    }
                }
            }
        }

        // Audio Devices
        Kirigami.Card {
            Layout.fillWidth: true
            Layout.preferredHeight: Kirigami.Units.gridUnit * 6

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Kirigami.Units.smallSpacing

                Kirigami.Heading {
                    text: "Audio Devices"
                    level: 3
                }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        text: "Input Device:"
                    }
                    ComboBox {
                        Layout.fillWidth: true
                        model: ["Default", "Virtual Input", "Real Microphone"]
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        text: "Output Device:"
                    }
                    ComboBox {
                        Layout.fillWidth: true
                        model: ["Default", "Virtual Output", "Real Speakers"]
                    }
                }
            }
        }

        // Service Status
        Kirigami.Card {
            Layout.fillWidth: true
            Layout.preferredHeight: Kirigami.Units.gridUnit * 12

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Kirigami.Units.smallSpacing

                Kirigami.Heading {
                    text: "Service Status"
                    level: 3
                }

                // Service status items would go here
                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: ListModel {
                        ListElement { name: "Capture"; status: "running"; statusColor: "green" }
                        ListElement { name: "Whisper"; status: "running"; statusColor: "green" }
                        ListElement { name: "Translate"; status: "stopped"; statusColor: "red" }
                        ListElement { name: "TTS"; status: "running"; statusColor: "green" }
                        ListElement { name: "Playback"; status: "running"; statusColor: "green" }
                    }
                    delegate: Kirigami.ListItem {
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: Kirigami.Units.smallSpacing
                            anchors.rightMargin: Kirigami.Units.smallSpacing

                            Rectangle {
                                width: 12
                                height: 12
                                radius: 6
                                color: statusColor
                                Layout.alignment: Qt.AlignVCenter
                            }

                            Label {
                                text: name
                                Layout.fillWidth: true
                            }

                            Label {
                                text: status
                                color: statusColor
                            }

                            Kirigami.Action {
                                text: status === "running" ? "Stop" : "Start"
                                icon.name: status === "running" ? "media-playback-stop" : "media-playback-start"
                            }
                        }
                    }
                }
            }
        }
    }
}