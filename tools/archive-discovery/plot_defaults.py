"""Project scientific plotting defaults; TeX/EB Garamond required."""
import matplotlib.pyplot as plt
plt.rcParams.update({'figure.dpi':120,'savefig.dpi':160,'font.size':14,
'font.family':'serif','font.serif':['EB Garamond'],'text.usetex':True,
 'text.latex.preamble':r"\usepackage{amsmath}\usepackage{ebgaramond}"})
