import caffe
import numpy as np
import argparse, pprint
from multiprocessing import Pool
import scipy.misc as scm
from os import path as osp
import my_pycaffe_io as mpio
import my_pycaffe as mp
from easydict import EasyDict as edict
import time
import glog
import pdb

def get_crop_coords(poke, H, W, crpSz, maxJitter=100):
	'''
		Crop a size of crpSz while assuring that the poke point is 
		inside a central box of side maxJitter in the crop
	'''
	maxJitter = min(maxJitter, crpSz)
	x1 = round(max(0, poke[0] - (crpSz -  maxJitter)/2 - maxJitter))
	x2 = max(x1, round(min(W - crpSz, max(0, poke[0] - (crpSz - maxJitter)/2))))
	y1 = round(max(0, poke[1] - (crpSz -  maxJitter)/2 - maxJitter))
	y2 = max(y1, round(min(H - crpSz, max(0, poke[1] - (crpSz - maxJitter)/2))))
	ySt   = int(np.random.random() * (y2 - y1) + y1)
	xSt   = int(np.random.random() * (x2 - x1) + x1)
	xEn, yEn = xSt + crpSz, ySt + crpSz
	pk     = [poke[0] - xSt, poke[1] - ySt, poke[2]]
	#Normalize pokes to range 0, 1
	pk[0]  = (pk[0] - crpSz/2.0)/float(crpSz)
	pk[1]  = (pk[1] - crpSz/2.0)/float(crpSz)
	pk[2]  = pk[2] - np.pi/2
	return xSt, ySt, xEn, yEn, pk


#ims   = np.zeros((128, 6, 192, 192)).astype(np.float32)
#pokes = np.zeros((128, 3, 1, 1)).astype(np.float32) 

#def image_reader_keys(dbNames, dbKeys, crpSz, isCrop, isGray=False):
def image_reader_keys(*args):
	dbNames, dbKeys, crpSz, isCrop, isGray = args
	t1 = time.time()
	bk,  ak,  pk   = dbKeys
	db  = mpio.MultiDbReader(dbNames)
	t15 = time.time()
	openTime = t15 - t1
	N   = len(bk)
	ims   = np.zeros((N, 6, crpSz, crpSz), np.uint8)
	#ims   = np.zeros((N, 6, crpSz, crpSz))
	pokes = np.zeros((N, 3, 1, 1), np.float32)
	t2  = time.time()
	preTime  = t2 - t15
	readTime, procTime, tranTime, cropFnTime, cropTime = 0, 0, 0, 0, 0
	for i in range(N):
		t3 = time.time()
		im1, im2, poke = db.read_key([bk[i], ak[i], pk[i]])
		t4 = time.time()
		readTime += t4 - t3
		im1  = im1.transpose((0,2,1))
		im2  = im2.transpose((0,2,1))
		poke = poke.reshape((1,3,1,1))
		t45  = time.time()
		tranTime += t45 - t4
		if isCrop:
			_, H, W = im1.shape
			x1, y1, x2, y2, newPoke = get_crop_coords(poke.squeeze(), H, W, crpSz)
			t47 = time.time()
			cropFnTime += t47 - t45
			#print (x1, y1, x2, y2)
			ims[i, 0:3] = im1[:, y1:y2, x1:x2]
			ims[i, 3:6] = im2[:, y1:y2, x1:x2]
			t48 = time.time()
			cropTime += t48 - t47
		else:
			ims[i, 0:3] = scm.imresize(im1, (crpSz, crpSz))
			ims[i, 3:6] = scm.imresize(im2, (crpSz, crpSz))
		pokes[i][...] = np.array(newPoke).reshape((3,1,1))
		t5 = time.time()
		procTime += t5 - t4
	#db.close()
	print '#####################'
	print 'Open-Time: %f, Pre-Time: %f, Read-Time: %f, Proc-Time: %f' % (openTime, preTime, readTime, procTime)
	print 'CropFnTime: %f, Crop-Time: %f, Transpose Time: %f' % (cropFnTime, cropTime, tranTime)
	print '#####################'
	return ims, pokes

	
class PythonPokeLayer(caffe.Layer):
	@classmethod
	def parse_args(cls, argsStr):
		parser = argparse.ArgumentParser(description='PythonPokeLayer')
		parser.add_argument('--before', default='', type=str)
		parser.add_argument('--after',  default='', type=str)
		parser.add_argument('--poke',  default='', type=str)
		parser.add_argument('--root_folder', default='', type=str)
		parser.add_argument('--mean_file', default='', type=str)
		parser.add_argument('--mean_type', default='3val', type=str)
		parser.add_argument('--batch_size', default=128, type=int)
		parser.add_argument('--crop_size', default=192, type=int)
		parser.add_argument('--is_gray', dest='is_gray', action='store_true')
		parser.add_argument('--no-is_gray', dest='is_gray', action='store_false')
		parser.add_argument('--is_mirror',  dest='is_mirror', action='store_true', default=False)
		parser.add_argument('--resume_iter', default=0, type=int)
		parser.add_argument('--max_jitter', default=0, type=int)
		parser.add_argument('--is_prefetch', default=0, type=int)
		parser.add_argument('--randSeed', default=3, type=int)
		args   = parser.parse_args(argsStr.split())
		print('Using Config:')
		pprint.pprint(args)
		return args	

	def __del__(self):
		self.pool_.terminate()

	def load_mean(self):
		self.mu_ = None
		if len(self.param_.mean_file) > 0:
			print ('READING MEAN FROM %s', self.param_.mean_file)
			if self.param_.mean_file[-3:] == 'pkl':
				meanDat  = pickle.open(self.param_.mean_file, 'r')
				self.mu_ = meanDat['mu'].astype(np.float32).transpose((2,0,1))
			else:
				#Mean is assumbed to be in BGR format
				self.mu_ = mp.read_mean(self.param_.mean_file)
				self.mu_ = self.mu_.astype(np.float32)
		if self.param_.mean_type == '3val':
			self.mu_   = np.mean(self.mu_, axis=[1,2]).reshape(1,3,1,1)
		elif self.param_.mean_type == 'img':
			ch, h, w = self.mu_.shape
			assert (h >= self.param_.crop_size and w >= self.param_.crop_size)
			y1 = int(h/2 - (self.param_.crop_size/2))
			x1 = int(w/2 - (self.param_.crop_size/2))
			y2 = int(y1 + self.param_.crop_size)
			x2 = int(x1 + self.param_.crop_size)
			self.mu_ = self.mu_[:,y1:y2,x1:x2]
			self.mu_ = self.mu_.reshape((1,) + self.mu_.shape)
		else:
			raise Exception('Mean type %s not recognized' % self.param_.mean_type)

	def setup(self, bottom, top):
		self.param_ = PythonPokeLayer.parse_args(self.param_str)
		rf  = self.param_.root_folder
		self.dbNames_ = [osp.join(rf, self.param_.before),
									   osp.join(rf, self.param_.after),
									   osp.join(rf, self.param_.poke)] 
		#Read Keys
		self.dbKeys_ = []
		for name in self.dbNames_:
			db   = mpio.DbReader(name)
			keys = db.get_key_all()
			self.dbKeys_.append(keys)
			db.close()
			del db 	
		self.stKey_  = 0
		self.numKey_ = len(self.dbKeys_[0]) 	
		#Poke layer has 2 input images
		self.numIm_ = 2
		self.lblSz_ = 3
		if self.param_.is_gray:
			self.ch_ = 1
		else:
			self.ch_ = 3
		top[0].reshape(self.param_.batch_size, self.numIm_ * self.ch_,
										self.param_.crop_size, self.param_.crop_size)
		top[1].reshape(self.param_.batch_size, self.lblSz_, 1, 1)
		#Load the mean
		self.load_mean()
		#If needed to resume	
		if self.param_.resume_iter > 0:
			N = self.param_.resume_iter * self.param_.batch_size
			N = np.mod(N, self.wfid_.num_)
			print ('SKIPPING AHEAD BY %d out of %d examples,\
						  BECAUSE resume_iter is NOT 0'\
							% (N, self.wfid_.num_))
		#Create the pool
		self.isPrefetch_ = bool(self.param_.is_prefetch)
		if self.isPrefetch_:
			self.pool_ = Pool(processes=1)
			self.jobs_ = []
	
		#Storing the image data	
		self.imData_ = np.zeros((self.param_.batch_size, 
						self.numIm_ * self.ch_,
						self.param_.crop_size, self.param_.crop_size), np.float32)
		self.labels_ = np.zeros((self.param_.batch_size, 
						self.lblSz_,1,1),np.float32)
		self.argList_ = []
		#Function to read the images
		self.readfn_ = image_reader_keys
		#Launch the prefetching
		if self.isPrefetch_:	
			self.launch_jobs()
		self.t_ = time.time()	

	def _make_arglist(self):
		self.argList_ = []
		enKey   = self.stKey_ + self.param_.batch_size
		if  enKey > self.numKey_:
			wrap = np.mod(enKey, self.numKey_)
			keys = range(self.stKey_, self.numKey_) +\
						 range(wrap)
			self.stKey_ = wrap
		else:
			keys = range(self.stKey_, enKey)
			self.stKey_ = enKey
		self.argList_ = [self.dbNames_, 
							 [[self.dbKeys_[0][k] for k in keys],
						   [self.dbKeys_[1][k] for k in keys],
							 [self.dbKeys_[2][k] for k in keys]],
               self.param_.crop_size, True, self.param_.is_gray]

	
	def launch_jobs(self):
		self._make_arglist()
		try:
			print ('PREFETCH STARTED')
			self.jobs_ = self.pool_.map_async(self.readfn_, self.argList_)
		except KeyboardInterrupt:
			print 'Keyboard Interrupt received - terminating in launch jobs'
			self.pool_.terminate()	

	def get_prefetch_data(self):
		t1 = time.time()
		if self.isPrefetch_:
			try:
				print ('GETTING PREFECH')
				res      = self.jobs_.get()
				print ('PREFETCH GOT')	
				im, self.labels_[...]  = res
			except:
				print 'Keyboard Interrupt received - terminating'
				self.pool_.terminate()
				raise Exception('Error/Interrupt Encountered')
		else:
			self._make_arglist()
			im, self.labels_[...] = self.readfn_(*self.argList_)
			#self.readfn_(*self.argList_)
	
		t2= time.time()
		tFetch = t2 - t1
		if self.mu_ is not None:	
			self.imData_[...] = im - self.mu_
		else:
			self.imData_[...] = im

	def forward(self, bottom, top):
		t1 = time.time()
		tDiff = t1 - self.t_
		#Load the images
		self.get_prefetch_data()
		top[0].data[...] = self.imData_
		t2 = time.time()
		tFetch = t2-t1
		#Read the labels
		top[1].data[:,:,:,:] = self.labels_
		if self.isPrefetch_:
			self.launch_jobs()
		t2 = time.time()
		glog.info('Prev: %f, fetch: %f forward: %f' % (tDiff,tFetch, t2-t1))
		self.t_ = time.time()

	def backward(self, top, propagate_down, bottom):
		""" This layer has no backward """
		pass
	
	def reshape(self, bottom, top):
		""" This layer has no reshape """
		pass


def test_poke_layer(isPlot=True):
	import vis_utils as vu
	import matplotlib.pyplot as plt
	fig     = plt.figure()
	defFile = 'test/poke_layer.prototxt'
	net     = caffe.Net(defFile, caffe.TEST)
	while True:
		data   = net.forward(blobs=['im', 'poke'])
		im, pk = data['im'], data['poke']
		if isPlot:
			for b in range(10):
				ax = vu.plot_pairs(im[b,0:3], im[b,3:6], isBlobFormat=True, chSwap=(2,1,0), fig=fig)
				ax[0].plot(int(pk[b][0]), int(pk[b][1]), markersize=10, marker='o')
				plt.draw()
				plt.show()
				ip = raw_input()
				if ip == 'q':
					return	
