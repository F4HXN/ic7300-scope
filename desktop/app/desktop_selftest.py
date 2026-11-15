"""Vérifier les dépendances et le modèle sans ouvrir la radio ou le profil utilisateur."""
from pathlib import Path
import sys

import numpy as np
from PyQt5 import QtCore
import pyqtgraph
import scipy
import serial
from cw_neural import ROOT, load_engine


def main():
    engine=load_engine()
    metadata=engine.load_metadata(ROOT/'models/deepcw/model.onnx.json')
    options=engine.ort.SessionOptions()
    options.intra_op_num_threads=2
    options.inter_op_num_threads=1
    session=engine.ort.InferenceSession(str(ROOT/'models/deepcw/model.onnx'),
                                      sess_options=options,providers=['CPUExecutionProvider'])
    spec=engine.audio_to_spectrogram(np.zeros(2*metadata['sample_rate'],np.float32),metadata)
    result=session.run([metadata['onnx_output_name']],{metadata['onnx_input_name']:spec})
    assert np.isfinite(result[0]).all()
    print('OK : Qt, audio numérique, port série et DeepCW (CPU). Aucun accès radio effectué.')
    print('Python '+sys.version.split()[0]+' ; Qt '+QtCore.QT_VERSION_STR+' ; ONNX Runtime '+engine.ort.__version__)

if __name__=='__main__':main()
