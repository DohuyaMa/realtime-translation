{ lib
, python3
, buildPythonApplication
, fetchPypi
, ...
}:

buildPythonApplication {
  name = "translator-tts";
  
  src = ./../../src;
  
  pyproject = true;
  build-system = [ "setuptools" ];
  
  propagatedBuildInputs = with python3.pkgs; [
    # TTS service specific dependencies
    pyttsx3
    gtts
    playsound
    kokoro-onnx
    numpy
    soundfile
    librosa
    
    pulsectl
    pyyaml
    python-dotenv
    loguru
    # Add any other TTS-specific dependencies
  ];
  
  buildInputs = with python3.pkgs; [
    # Build-time dependencies
  ];
  
  # Define console script for TTS service
  pythonPath = with python3.pkgs; [
    pyttsx3
    gtts
    playsound
    kokoro-onnx
    numpy
    soundfile
    librosa
    
    pulsectl
    pyyaml
    python-dotenv
    loguru
  ];
  
  nativeBuildInputs = with python3.pkgs; [
    makeWrapper
  ];
  
  postInstall = ''
    mkdir -p $out/bin
    makeWrapper ${python3.interpreter} $out/bin/translator-tts \
      --add-flags "-c 'import sys; sys.path.insert(0, \"$out/lib/${python3.libPrefix}/site-packages\"); from tts.tts_service import main; main()'"
  };
  
  passthru = {
    exeName = "translator-tts";
  };
  
  meta = with lib; {
    description = "Text-to-speech service for real-time translation";
    homepage = "https://github.com/dmaslo/real-time-transletor";
    license = licenses.mit;
    maintainers = [ ];
  };
}