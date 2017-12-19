
# Libraries
import numpy as np
import pandas as pd
import sys
import time
import gzip
import shutil
import os
import imp
import copy
import math
import matplotlib.pyplot as plt

from PIL import Image

# Project Library
from src.lib import FCC_lib_data_v1 as ld
from src.lib import FCC_lib_preprocess_v1 as pp
from src.lib import FCC_lib_models_NN_torch_v1 as lm
from src.lib import FCC_lib_2Dimg_v1 as ltt

MODEL_ID = "NN_QxG02"
STAGE    = "S5_FC"

class Model(object):
    
    def __init__(self):
        self.reset_variables()
        self.reset_parameters_DATA()
        self.reset_parameters_S1_MODEL()
        self.reset_parameters_S7_MODEL()
        self.reset_parameters_MODEL()
        self.reset_parameters_TRANSFORMATIONS()
        self.reset_parameters_TRAIN()
        self.reset_parameters_PREDICT()
        
    def reset_parameters_DATA(self):
        # Parameters: DATA
        self.ppFUNC = pp.ppPCH01
        self.size = (224, 224)
        self.channels = 3
        self.DT_mean = np.array([123.675, 116.28, 103.53])
        self.DT_std = np.array([58.395, 57.12, 57.375])
        self.DT_zero = (np.array([0] * self.channels) - self.DT_mean) / self.DT_std
    
    def reset_parameters_S1_MODEL(self):
        self.S1_STAGE, self.S1_MODEL_ID = 'S1_ROI', 'NN_AxC01'
        S1_src_file = 'src/{}_models/{}_{}_model.py'.format(self.S1_STAGE, self.S1_STAGE, self.S1_MODEL_ID)
        self.S1_Model = imp.load_source('', S1_src_file).Model('test')
        filename = os.path.join(self.S1_Model.path_predictions, '{}_{}_pred.csv.gz'\
                                .format(self.S1_STAGE, self.S1_MODEL_ID))
        try:
            self.S1_Model_DF = pd.read_csv(filename)
        except:
            self.S1_Model_DF = None

    def reset_parameters_S7_MODEL(self):
        self.S7_STAGE, self.S7_MODEL_ID = 'S7_FL', 'NN_AxA10'
        S7_src_file = 'src/{}_models/{}_{}_model.py'.format(self.S7_STAGE, self.S7_STAGE, self.S7_MODEL_ID)
        self.S7_Model = imp.load_source('', S7_src_file).Model()
            
    def reset_parameters_MODEL(self):
        # Parameters: MODEL   
        from src.lib import FCC_lib_models_NN_torch_v1 as lm
        self.NNmodel_FUNC = lm.pretrain_vgg11_v0
        self.isz2D = self.size  # Size of 2D patches
        self.model_size = (self.channels, self.isz2D, self.Data.clss_nb)  #channels, ISZ, classes
        self.model_desc = "VGG-11"
        self.model_args = {'criterion': lm.BCEWithLogitsLoss(),
                           'interm': 256,
            }
    
    def reset_parameters_TRANSFORMATIONS(self):
        # Parameters: TRANSFORMATIONS
        self.data_transforms = {
            'train': ltt.Compose([
                        ltt.RandomBright((0.8,1.2), p=1.0),
                        ltt.RandomContrast((0.5,1.5), p=1.0),
                        ltt.RandomColor((0.75,1.5), p=1.0),
                        ltt.RandomRotate(10),
                        ltt.RandomShuffleChannels(),
                        ltt.RandomShear((0,0.15), method='PIL', p=0.5),
                        ltt.RandomShear((0.15,0), method='PIL', p=0.5),
                        ltt.RandomVerticalFlip(p=0.5),
                        ltt.RandomHorizontalFlip(p=0.5),
                        
                        ltt.Scale(self.size),
                        ltt.ToArray(np.float32, img_DT_mean = self.DT_mean, img_DT_std = self.DT_std),
                        ]),
            'valid': ltt.Compose([
                        ltt.Scale(self.size),
                        ltt.ToArray(np.float32, img_DT_mean = self.DT_mean, img_DT_std = self.DT_std),
                        ]),
            'test': ltt.Compose([
                        ltt.Scale(self.size),
                        ltt.ToArray(np.float32, img_DT_mean = self.DT_mean, img_DT_std = self.DT_std),
                        ]),
        }
        
    def reset_parameters_TRAIN(self):
        # Parameters: TRAINING
        self.fold_column = 'Fs3'
        self.seed = 0
        self.gen_comm_params = {'seed': None} 
        self.train_gen_params = self.gen_comm_params.copy() 
        self.valid_gen_params = self.gen_comm_params.copy()    
        self.train_gen_params = self.gen_comm_params.copy() 
        self.valid_gen_params.update({'shuffle': False, })                           
    
    def reset_parameters_PREDICT(self): 
        self.predict_gen_params = self.gen_comm_params.copy() 
        self.predict_gen_params.update({'shuffle':False, 'predicting':True})
        self.predict_batch_size = 128
        
    def reset_variables(self):
        # Initializations
        self.dsetID = None
        self.Data = ld.FishDATA()
        self.img_raw = None
        self.img = None
        self.info = None
        
        self.output_dir = str(self.Data.path_settings['path_outputs_{}'.format(STAGE)])
        self.NNmodel = None
        self.stage = STAGE
        self.model_id = MODEL_ID
        self.weights_format = '{}_{}_{}_model'.format(self.stage, self.model_id, '{fold_id}')
        self.path_predictions = os.path.join(self.output_dir, self.model_id)
        self.weights_file = None
        self.prev_foldID = None
        
        
    def read_image(self, itype, image_id, 
                   frame = 'example',  # int, 'all', 'example'(0 or max_size)
                               #'all_labeled' --> only with annotations
                               #'all_train' --> only if training
                   read_labels=False, split_wrap_imgs = False, seed=None, 
                   use_cache=None, verbose=False):
        '''Custom read_image function for this model.
        '''

        start_time_L1 = time.time()
        
        # Start data class & variables
        Data = self.Data
        labels=[] if read_labels else None
        info={}
        
        # Read image.
        vidD = self.Data.load_vidDATA(itype, image_id)
        
        # Read annotations
        df = self.Data.annotations
        mini_df = df[df.video_id == image_id]
        nb_frames = len(mini_df)
        
        # Create frames list
        if frame == 'all':
            frames = range(len(vidD.vi))
        elif frame == 'example':
            frames = [0,]
        elif frame == 'all_labeled' and nb_frames > 0:
            frames = mini_df.frame.values.tolist()
        elif frame == 'all_train' and nb_frames > 0:
            frames = mini_df[mini_df.fish_number >= 0].frame.values.tolist()
        else:
            frames = [int(frame),]
        
        # Read bbox from S1_Model
        use_cache = self.Data.exec_settings['cache'] == "True" if use_cache is None else use_cache
        
        # Read fish_patches_coords
        fish_bbox = self.S7_Model.get_predictions(itype, image_id, return_imgs=False, 
                                                  use_cache=use_cache, verbose=False)
        
        # Extract patches
        patches = []
        for i_frame in frames:
            # Only use cache images if frame in annotations
            use_cache_pp = use_cache and (i_frame in mini_df.frame.values.tolist())
            patch = self.ppFUNC(itype, image_id, i_frame,
                                Data = Data, vidD = vidD, S1_Model_DF=self.S1_Model_DF,
                                use_cache = use_cache_pp, verbose = False)
            
            i_bbox = fish_bbox[fish_bbox.ich == i_frame]
            
            if i_bbox.iloc[0].length > 0:
                patch = Data.extract_patch_PIL(patch,(i_bbox.iloc[0].xc, i_bbox.iloc[0].yc), i_bbox.iloc[0].ang, 
                                   size=(i_bbox.iloc[0].length, int(i_bbox.iloc[0].length/2.0)))
            else:
                patch = Image.new('RGB', (10, 10))
            
            patches.append(patch)
            
            if read_labels:
                label = mini_df[mini_df.frame == i_frame][self.Data.clss_names]
                if len(label) == 0:
                    labels.append(np.nan)
                else:
                    labels.append(label.values[0].astype(np.uint16))

        
        # Include usefull information
        info = {'meta': vidD.vi._meta}
    
        # wrap results
        if len(patches)>1:
            if split_wrap_imgs:
                wrap_img = [patches, labels, info]
            else:
                wrap_img = [[patches[s1], labels[s1], info] for s1 in range(len(patches))]
        else:
            wrap_img = [patches[0], labels[0], info]
        
        if verbose:
            print("Read image {} in {:.2f} s".format(image_id, (time.time() - start_time_L1)/1))
    
        return wrap_img

    def batch_generator(self, datafeed, batch_size=1, params={}):
        
        # Parameters
        seed = params.get('seed', None)
        shuffle = params.get('shuffle', True)
        predicting = params.get('predicting', False)
        
        sample_index = np.arange(len(datafeed))
        number_of_batches = np.ceil(len(sample_index)/batch_size)
        
        if seed is not None:
            np.random.seed(seed)
        if shuffle:
            np.random.shuffle(sample_index)
        
        counter = 0
        while True:
            batch_index = sample_index[batch_size*counter:batch_size*(counter+1)]
            x_trn, y_trn = datafeed[batch_index]
            
            # Yield
            counter += 1
            if predicting:
                yield x_trn
            else:
                yield x_trn, y_trn
                
            if (counter == number_of_batches):
                if shuffle:
                    np.random.shuffle(sample_index)
                counter = 0  
        
    def get_NNmodel(self, model_size=None, model_args=None, NNmodel_FUNC=None):
        
        model_size = self.model_size if model_size is None else model_size
        model_args = self.model_args if model_args is None else model_args
        NNmodel_FUNC = self.NNmodel_FUNC if NNmodel_FUNC is None else NNmodel_FUNC
        
        NNmodel = NNmodel_FUNC(channels = model_size[0], isz = model_size[1], classes = model_size[2], 
                               args_dict = model_args)
        self.NNmodel = NNmodel
        
        return NNmodel
    
    def load_weights(self, weights_filename, weights_path=None, verbose=False):
        weights_path = self.output_dir if weights_path is None else weights_path
        self.weights_file = '{}{}.torch'.format(weights_path, weights_filename)
        if self.NNmodel is None:
            self.get_NNmodel()
        self.NNmodel.load_model(self.weights_file)
        if verbose:
            print('  Read model: {}'.format(self.weights_file))
            
    def predict(self, image, pred_type='test'):
        '''
        image: img (PIL) or image_id
        '''
        img = image if isinstance(image, Image.Image) else self.read_image(image)[0]
        
        if self.NNmodel is None:
            self.get_NNmodel()
        if self.weights_file is None:
            sys.exit("Weights not loaded")
        
        #apply transformations
        timg = self.data_transforms[pred_type](img)
        
        # make batch size = 1
        timg = timg[np.newaxis, ...]
        
        # Predict
        pred = self.NNmodel.predict(timg)
        
        # Change predictions dtype
        pred = pred.astype(np.float16)

        return pred
    
    def predict_BATCH(self, images, pred_type='test', batch_size = None):
        '''
        images: list(img (np.array)) or image_id
        '''
        
        if self.NNmodel is None:
            self.get_NNmodel()
        if self.weights_file is None:
            sys.exit("Weights not loaded")
        
        #apply transformations
        timgs = self.data_transforms[pred_type](images)
        
        # convert to array
        timgs = np.array(timgs)
        
        # predict in batches
        preds = []
        batch_size = self.predict_batch_size if batch_size is None else batch_size
        for start in range(0, timgs.shape[0], batch_size):
            end = min(start+batch_size, timgs.shape[0])
            #pred = self.NNmodel.predict_on_batch(timgs[start:end])
            pred = self.NNmodel.predict(timgs[start:end])
            preds.append(pred)
        preds = np.vstack(preds)    
        
        # Change predictions dtype
        preds = preds.astype(np.float16)

        return preds    
    
    def get_predictions(self, itype, image_id,
                        return_imgs = False, avoid_read_weights=False, return_score = False, 
                        use_cache=None, force_save=False, verbose=True):
        
        start_time_L1 = time.time()
        use_cache = self.Data.exec_settings['cache'] == "True" if use_cache is None else use_cache
        pred = None
        score = None
        score_txt = 'log_loss'
        
        if use_cache & (not force_save):
            try:
                file_to_load = os.path.join(self.path_predictions, itype, '{}_{}_pred.npy.gz'.format(itype, image_id))
                with gzip.open(file_to_load, 'rb') as f:
                    pred = np.load(f)
                if not return_imgs:
                    if verbose:
                        print("Read prediction {}_{} in {:.2f} s".format(itype, image_id, 
                              (time.time() - start_time_L1)/1))
                    return pred
            except:
                if verbose:
                    print("File not in cache")
                    
        imgs, labels, info = self.read_image(itype, image_id, frame = 'all', split_wrap_imgs = True,
                                         read_labels=(itype=='train'), verbose=verbose)
        
        if pred is None:
            
            #get weights
            if (self.weights_file is None) or not avoid_read_weights:
                self.dsetID = ld.read_dsetID() if self.dsetID is None else self.dsetID
                fold_id = self.dsetID.loc[(self.dsetID.video_id == image_id) & (self.dsetID.itype == itype), 
                                          self.fold_column]
                fold_id = fold_id.values[0]
                if self.prev_foldID != fold_id:
                    weight_file = self.weights_format.format(fold_id=fold_id)
                    self.load_weights(weight_file, verbose=verbose)
                    self.prev_foldID = fold_id            
            
            # predict
            pred = self.predict_BATCH(imgs)
            
            # Save cache
            if use_cache|force_save:
                if not os.path.exists(os.path.join(self.path_predictions, itype)):
                    os.makedirs(os.path.join(self.path_predictions, itype))
                file_to_save = os.path.join(self.path_predictions, itype, '{}_{}_pred.npy'.format(itype, image_id))    
                np.save(file_to_save, pred)
                with open(file_to_save, 'rb') as f_in, gzip.open(file_to_save + '.gz', 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
                os.remove(file_to_save)
                        
        
        # evaluate
        if labels is not None:
            from sklearn.metrics import roc_auc_score
            select = [np.logical_not(np.all(np.isnan(s1))) for s1 in labels]
            np_labels = [s1 for s1, s2 in zip(labels, select) if s2]
            np_labels = np.vstack(np_labels)
            np_preds = [s1 for s1, s2 in zip(pred, select) if s2]
            np_preds = np.vstack(np_preds)
            y_true = (np_labels).astype(np.float32)
            y_pred = (np_preds).astype(np.float32)
            score = []
            for i in range(y_pred.shape[0]):
                if np.sum(y_true[i]) > 0:
                    tmp_score = roc_auc_score(y_true[i], y_pred[i])
                else:
                    tmp_score = np.nan
                score.append(tmp_score)
            score = np.nanmean(score)
        
        if verbose: 
            if score is not None:
                print("Read prediction {}_{} ({}: {:.5f}) in {:.2f} s".format(itype, image_id, score_txt, score, 
                      (time.time() - start_time_L1)/1))        
            else:
                print("Read prediction {}_{} in {:.2f} s".format(itype, image_id, (time.time() - start_time_L1)/1))        
        
        if return_imgs:
            if return_score:
                return pred, imgs, labels, score
            else:
                return pred, imgs, labels
            
        if return_score:
            return pred,  score
        else:
            return pred
        
    def get_predictions_BATCH(self, itype_list, image_id_list, imgs_list, batch_size = None, verbose=False):
        '''
        Predict from a list of imgs (outputs from self.read_image)
        '''
        
        for itype, image_id, imgs in zip(itype_list, image_id_list, imgs_list):
            
            #get weights
            if (self.weights_file is None):
                self.dsetID = ld.read_dsetID() if self.dsetID is None else self.dsetID
                fold_id = self.dsetID.loc[(self.dsetID.video_id == image_id) & (self.dsetID.itype == itype), 
                                          self.fold_column]
                fold_id = fold_id.values[0]
                if self.prev_foldID != fold_id:
                    weight_file = self.weights_format.format(fold_id=fold_id)
                    self.load_weights(weight_file, verbose=False)
                    self.prev_foldID = fold_id            
            
            # predict
            pred = self.predict_BATCH(imgs, batch_size = batch_size)
            
            # Save cache
            if not os.path.exists(os.path.join(self.path_predictions, itype)):
                os.makedirs(os.path.join(self.path_predictions, itype))
            file_to_save = os.path.join(self.path_predictions, itype, '{}_{}_pred.npy'.format(itype, image_id))    
            np.save(file_to_save, pred)
            with open(file_to_save, 'rb') as f_in, gzip.open(file_to_save + '.gz', 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
            os.remove(file_to_save)
    

    def show_imgs(self, imgs, labels=None, preds=None, transform_type=None, grid = None, size=(12,6), title=""):
        
        if transform_type is not None:
            tt = copy.deepcopy(self.data_transforms[transform_type])
            tt.transforms = tt.transforms[:-1]  # Eliminate 'ToArray'
            imgs = tt(imgs)
        
        if isinstance(imgs, list):
            nb_frames = len(imgs)
            labels = labels if labels is not None else [labels]*nb_frames
            preds = preds if preds is not None else [preds]*nb_frames
        else:
            nb_frames = 1
            imgs = [imgs,]
            labels = [labels,]
            preds = [preds,]
            
        # plot images
        if grid is None:
            nbx = int(math.sqrt(nb_frames))
            nby = int(np.ceil(nb_frames/float(nbx)))
        else:
            nbx, nby = grid
        fig,axes = plt.subplots(nbx,nby,figsize=size)
        fig.suptitle(title)
        ax = axes.ravel() if nb_frames>1 else [axes,]
        
        for i in range(nb_frames): 
            ax[i].imshow(imgs[i], cmap='gray')
            try:
                i_label = labels[i]
                i_label = self.Data.clss_names[np.argmax(i_label)]
            except:
                i_label = np.nan
            try:
                i_pred = preds[i]
                i_pred = self.Data.clss_names[np.argmax(i_pred)]
            except:
                i_pred = np.nan
            ititle = 'T: {} - P: {}'.format(i_label, i_pred)
            ax[i].set_title(ititle) 
            if i == len(ax)-1:
                break
        plt.show()
