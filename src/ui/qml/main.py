"""Kirigami UI entry point for the real-time translation system."""
import sys
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import QUrl, QObject, Signal, Slot
import os

# Add the src directory to the Python path so we can import our modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from ..controller import ConcreteTranslatorController
from ..adapters import DirectAdapter


class KirigamiController(QObject):
    """QML-compatible controller for the Kirigami UI."""
    
    def __init__(self):
        super().__init__()
        
        # Initialize the controller with DirectAdapter (for now)
        adapter = DirectAdapter()
        self._controller = ConcreteTranslatorController(adapter)
        
    @Slot(result=str)
    def get_status(self):
        """Get system status."""
        status = self._controller.get_status()
        # Return as JSON string for QML
        import json
        return json.dumps(status)
    
    @Slot(result=bool)
    def start_pipeline(self):
        """Start the translation pipeline."""
        return self._controller.start_pipeline()
    
    @Slot(result=bool)
    def stop_pipeline(self):
        """Stop the translation pipeline."""
        return self._controller.stop_pipeline()
    
    @Slot(str, result=bool)
    def start_service(self, service_name):
        """Start a specific service."""
        return self._controller.start_service(service_name)
    
    @Slot(str, result=bool)
    def stop_service(self, service_name):
        """Stop a specific service."""
        return self._controller.stop_service(service_name)
    
    @Slot(str, str, result=bool)
    def set_languages(self, source_lang, target_lang="en"):
        """Set source and target languages."""
        return self._controller.set_languages(source_lang, target_lang)


def main():
    """Main entry point for the Kirigami UI."""
    app = QGuiApplication(sys.argv)
    
    # Create the controller
    controller = KirigamiController()
    
    engine = QQmlApplicationEngine()
    
    # Expose the controller to QML
    engine.rootContext().setContextProperty("kirigamiController", controller)
    
    # Load the main QML file
    qml_file = os.path.join(os.path.dirname(__file__), 'Main.qml')
    engine.load(QUrl.fromLocalFile(qml_file))
    
    if not engine.rootObjects():
        sys.exit(-1)
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()