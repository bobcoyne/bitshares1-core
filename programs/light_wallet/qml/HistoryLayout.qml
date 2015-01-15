import QtQuick 2.4
import QtQuick.Controls 1.3
import QtQuick.Layouts 1.1

import Material 0.1

Page {
   title: assetSymbol + " " + qsTr("Transactions")

   property real minimumWidth: 30
   property real minimumHeight: units.dp(80)
   property string accountName
   property string assetSymbol

   ScrollView {
      id: historyScroller
      anchors.fill: parent
      flickableItem.interactive: true
      // @disable-check M16
      verticalScrollBarPolicy: Qt.platform.os in ["android", "ios"]? Qt.ScrollBarAsNeeded : Qt.ScrollBarAlwaysOff

      ListView {
         id: historyList
         model: wallet.account.transactionHistory(assetSymbol)

         Connections {
            target: wallet
            onSynced: historyList.model = wallet.account.transactionHistory(assetSymbol)
         }

         delegate: Rectangle {
            width: parent.width - visuals.margins * 2
            height: transactionSummary.height
            anchors.horizontalCenter: parent.horizontalCenter
            property var trx: model

            Rectangle { width: parent.width; height: 1; color: "darkgrey"; visible: index }
            ColumnLayout {
               id: transactionSummary
               width: parent.width

               property string timestamp: model.modelData.timestamp

               Item { Layout.preferredHeight: visuals.margins }
               Repeater {
                  id: ledgerRepeater
                  model: trx.modelData.ledger
                  delegate: RowLayout {
                     width: parent.width
                     property bool incoming: receiver === accountName

                     Item { Layout.preferredWidth: visuals.margins }
                     RoboHash {
                        name: incoming? sender : receiver
                        Layout.preferredWidth: units.dp(100)
                     }
                     Column {
                        Layout.fillWidth: true
                        Label {
                           text: memo
                           font.pixelSize: units.dp(16)
                        }
                        Item { width: 1; height: units.dp(8) }
                        Label {
                           text: (index === 0)? (transactionSummary.timestamp) : ""
                           font.pixelSize: units.dp(16)
                        }
                     }
                     Label {
                        text: amount + " " + symbol
                        color: incoming? "green" : "red"
                        font.pixelSize: units.dp(16)
                     }
                     Item { Layout.preferredWidth: visuals.margins }
                  }
               }
               Item { Layout.preferredHeight: visuals.margins }
            }
            Ink { anchors.fill: parent }
         }
      }
   }
}
