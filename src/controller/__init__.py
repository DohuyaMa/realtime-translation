"""Controller module for the real-time translation system."""
from .controller import TranslatorController
from .translator_controller import ConcreteTranslatorController

__all__ = ['TranslatorController', 'ConcreteTranslatorController']