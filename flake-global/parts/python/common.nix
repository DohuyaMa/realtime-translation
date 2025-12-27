{ lib
, buildPythonApplication
, python3
, src
, pname
, dependencies
}:

buildPythonApplication {
  inherit pname src;
  version = "0.1.0";

  pyproject = true;

  propagatedBuildInputs = dependencies;

  meta = with lib; {
    license = licenses.mit;
    maintainers = [ ];
  };
}