{ lib
, python3
, buildPythonApplication
, fetchPypi
, ...
}:

buildPythonApplication {
  name = "translator-translate";
  
  src = ./../../src;
  
  pyproject = true;
  build-system = [ "setuptools" ];
  
  propagatedBuildInputs = with python3.pkgs; [
    # Translation service specific dependencies
    transformers
    torch
    sentencepiece
    sacremoses
    numpy
    onnxruntime
    soundfile
    librosa
    
    pulsectl
    pyyaml
    python-dotenv
    loguru
    # Add any other translation-specific dependencies
  ];
  
  buildInputs = with python3.pkgs; [
    # Build-time dependencies
  ];
  
  # Define console script for translation service
  pythonPath = with python3.pkgs; [
    transformers
    torch
    sentencepiece
    sacremoses
    numpy
    onnxruntime
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
    makeWrapper ${python3.interpreter} $out/bin/translator-translate \
      --add-flags "-c \"import sys; sys.path.insert(0, '$out/lib/${python3.libPrefix}/site-packages'); from translate.translate_service import main; main()\""
  };
  
  passthru = {
    exeName = "translator-translate";
  };
  
  meta = with lib; {
    description = "Translation service for real-time translation system";
    homepage = "https://github.com/dmaslo/real-time-transletor";
    license = licenses.mit;
    maintainers = [ ];
  };
}