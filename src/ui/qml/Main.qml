import QtQuick
import QtQuick.Controls
import org.kde.kirigami as Kirigami

Kirigami.ApplicationWindow {
    id: root
    width: 900
    height: 600
    visible: true
    title: "RT Translator"

    pageStack.initialPage: Dashboard {}
}