{ pkgs ? import <nixpkgs> {} }:
let
  # PyPI-пакет `dracopy` (импортируется как `DracoPy`): биндинг к Google Draco,
  # нужен rbxmesh.py для декода Draco-сжатых COREMESH-чанков мешей v6/v7
  # (новые MeshPart'ы) при конверсии под старые клиенты. В nixpkgs его нет,
  # поэтому собираем из PyPI: это самодостаточный C++-экстеншн с исходниками
  # draco внутри.
  dracopy = pkgs.python3Packages.buildPythonPackage rec {
    pname = "dracopy";
    version = "2.1.0";
    pyproject = true;

    src = pkgs.fetchPypi {
      inherit pname version;
      sha256 = "677fb7008468b856fdbbb431470ce102f00b82d5991b2a5c618734fbb908d635";
    };

    nativeBuildInputs = with pkgs.python3Packages; [
      setuptools scikit-build cython numpy wheel cmake
    ] ++ [ pkgs.ninja ];
    # setup_requires=['cython','cmake'] в setup.py пытается качать яйца из
    # сети — в nix-сборке сети нет, поэтому всё перечислено выше явно.
    # cmake нужен только skbuild'у (source dir = ./draco), поэтому штатный
    # configurePhase nix отключаем — иначе он падает на корне без CMakeLists.
    dontUseCmakeConfigure = true;
    doCheck = false;
  };

  rfd-python = pkgs.python3.withPackages (ps: with ps; [
    pygobject3
    websocket-client
    requests
    trustme
    urllib3
    pyzstd
    py7zr
    lz4
    numpy
    dracopy
  ]);
in
pkgs.mkShell {
  buildInputs = with pkgs; [
    umu-launcher
    wineWow64Packages.stable
    winetricks
    cage
    cabextract unzip p7zip
    rfd-python
  ];
  shellHook = ''
   echo "export WINEPREFIX="$PWD/.wine-rfd""
   echo "this is prorably for wineprefix if you want --backend wine sorry"
   echo "export WINEDEBUG=-all"
   echo "this is for no logs idk but yeah"
   echo "[ -d "$WINEPREFIX" ] || wineboot --init"
   echo "this is wineboot init a prefix for"
   echo "guys download please this for work prorably for wineprefix"
   echo "winetricks vcrun2019"
  '';
}
