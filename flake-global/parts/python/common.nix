{ lib
, python3
, src
, pname
, dependencies
}:

python3.pkgs.buildPythonApplication {
  inherit pname src;
  version = "0.1.0";

  pyproject = true;

  propagatedBuildInputs = dependencies;

  meta = with lib; {
    license = licenses.mit;
    maintainers = [ ];
  };
}