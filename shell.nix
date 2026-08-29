{ pkgs ? import <nixpkgs> {} }:
let
  rfd-python = pkgs.python3.withPackages (ps: with ps; [
    pygobject3
    websocket-client
    requests
    trustme
    urllib3
    pyzstd
    py7zr
    lz4
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
