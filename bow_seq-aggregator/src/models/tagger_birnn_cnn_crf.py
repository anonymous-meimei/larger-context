"""BiLSTM/BiGRU + char-level CNN  + CRF tagger model"""
import math
import torch
import torch.nn as nn
import os
from src.models.tagger_base import TaggerBase
from src.layers.layer_word_embeddings import LayerWordEmbeddings, LayerWordEmbeddings_Rand,LayerWordEmbeddings_ExtendVocab
from src.layers.layer_bivanilla import LayerBiVanilla
from src.layers.layer_bilstm import LayerBiLSTM
from src.layers.layer_bigru import LayerBiGRU
from src.layers.layer_Wcnn import LayerWCNN
from src.layers.layer_char_embeddings import LayerCharEmbeddings
from src.layers.layer_elmo_embeddings import LayerElmoEmbeddings
from src.layers.layer_char_cnn import LayerCharCNN
from src.layers.layer_char_lstm import LayerCharLSTM
from src.layers.layer_crf import LayerCRF
from src.layers.layer_selfAttn import LayerSelfAttn
from src.layers.layer_bert_embeddings import LayerBertEmbeddings
from src.layers.layer_flair_embeddings import LayerFlairEmbeddings

from pytorch_pretrained_bert.file_utils import PYTORCH_PRETRAINED_BERT_CACHE

from src.layers.layer_transformer_torch import TransformerEncoder,TransformerEncoderLayer

class TaggerBiRNNCNNCRF(TaggerBase):
    """TaggerBiRNNCNNCRF is a model for sequences tagging that includes recurrent network + conv layer + CRF."""
    def __init__(self, args, word_seq_indexer, tag_seq_indexer, class_num, batch_size=1, rnn_hidden_dim=100,
                 freeze_word_embeddings=False, dropout_ratio=0.5, rnn_type='GRU', gpu=-1,
                 freeze_char_embeddings = False, char_embeddings_dim=25, word_len=20, char_cnn_filter_num=30,
                 char_window_size=3):
        super(TaggerBiRNNCNNCRF, self).__init__(word_seq_indexer, tag_seq_indexer, gpu, batch_size)
        self.tag_seq_indexer = tag_seq_indexer
        self.class_num = class_num
        self.rnn_hidden_dim = rnn_hidden_dim
        self.freeze_embeddings = freeze_word_embeddings
        self.dropout_ratio = dropout_ratio
        self.rnn_type = rnn_type
        self.gpu = gpu
        self.word_embeddings_layer = LayerWordEmbeddings(args,word_seq_indexer, gpu, freeze_word_embeddings)
        self.freeze_char_embeddings = freeze_char_embeddings
        self.char_embeddings_dim = char_embeddings_dim
        self.word_len = word_len
        self.char_cnn_filter_num = char_cnn_filter_num
        self.char_window_size = char_window_size

        self.args = args
        self.if_bert = args.if_bert
        self.if_flair = args.if_flair



        self.dropout = torch.nn.Dropout(p=dropout_ratio)

        # self.bert_embeddings_dim = self.args.bert_embeddings_dim

        emb_models_dim = []
        print('load embedding...')
        if args.if_bert:
            # print('PYTORCH_PRETRAINED_BERT_CACHE',PYTORCH_PRETRAINED_BERT_CACHE)
            cache_dir = args.cache_dir if args.cache_dir else os.path.join(str(PYTORCH_PRETRAINED_BERT_CACHE),
                                                                           'distributed_{}'.format(args.local_rank))

            # cache_dir =os.path.join(str(PYTORCH_PRETRAINED_BERT_CACHE),'distributed_{}'.format(args.local_rank))
            print('cache_dir', cache_dir)
            # cache_dir = '/home/jlfu/.pytorch_pretrained_bert/distributed_-1'
            #cache_dir='emb/bert_model_cache/bert_cache.hdf5',
            self.bert_embeddings_layer = LayerBertEmbeddings.from_pretrained(args.bert_model,
                                                                             cache_dir=cache_dir,
                                                                             num_labels=class_num)
            # self.bert_embeddings_layer = LayerBertEmbeddings(gpu, freeze_bert_embeddings=True)

            reduce_dim =False
            if reduce_dim:
                self.W_bert = nn.Linear(args.bert_output_dim,256)
                emb_models_dim.append(256)
            else:
                emb_models_dim.append(args.bert_output_dim)
        if args.if_flair:
            self.flair_embeddings_layer =LayerFlairEmbeddings(gpu)
            reduce_dim =True
            if reduce_dim == True:
                self.W_flair = nn.Linear(self.flair_embeddings_layer.output_dim,256).cuda()
                emb_models_dim.append(256)
            else:
                emb_models_dim.append(self.flair_embeddings_layer.output_dim)
        if args.if_elmo:
            self.elmo_embeddings_layer = LayerElmoEmbeddings(args, gpu, args.options_file, args.weight_file,freeze_char_embeddings, word_len)

            elmo_reduce_dim =False
            if elmo_reduce_dim:
                # self.W_elmo = nn.Linear(self.elmo_embeddings_layer.output_dim,256)
                # emb_models_dim.append(256)

                self.W_elmo = nn.Linear(self.elmo_embeddings_layer.output_dim,self.word_embeddings_layer.output_dim)
                emb_models_dim.append(self.word_embeddings_layer.output_dim)
            else:
                emb_models_dim.append(self.elmo_embeddings_layer.output_dim)

        # if args.if_glove:
        #     self.word_embeddings_layer = LayerWordEmbeddings(word_seq_indexer, gpu, freeze_word_embeddings)
        #     emb_models_dim.append(self.word_embeddings_layer.output_dim)
        self.if_word = False
        if args.if_wordEmbRand==True and args.if_glove==False:
            self.word_embeddings_layer = LayerWordEmbeddings_Rand(word_seq_indexer, gpu, freeze_word_embeddings)
            emb_models_dim.append(self.word_embeddings_layer.output_dim)
            print('load random word emb ')
            self.if_word = True
        elif args.if_wordEmbRand==False and args.if_glove==True:
            self.word_embeddings_layer = LayerWordEmbeddings(args,word_seq_indexer, gpu, freeze_word_embeddings)
            emb_models_dim.append(self.word_embeddings_layer.output_dim)
            print('load glove word emb ')
            self.if_word = True
        else:
            print('can only use one word embedding (random or glove)')



        self.if_char = False
        if args.if_cnnChar==True and args.if_lstmChar==False:
            self.char_embeddings_layer = LayerCharEmbeddings(gpu, char_embeddings_dim, freeze_char_embeddings,
                                                             word_len, word_seq_indexer.get_unique_characters_list())
            self.char_layer = LayerCharCNN(gpu, char_embeddings_dim, char_cnn_filter_num, char_window_size,
                                               word_len)
            emb_models_dim.append(self.char_layer.output_dim)
            self.if_char = True

        elif args.if_cnnChar==False and args.if_lstmChar==True:
            self.char_embeddings_layer = LayerCharEmbeddings(gpu, char_embeddings_dim, freeze_char_embeddings,
                                                             word_len, word_seq_indexer.get_unique_characters_list())
            self.char_layer = LayerCharLSTM(gpu,char_embeddings_dim, self.char_lstm_hidden_dim, word_len)
            emb_models_dim.append(self.char_layer.output_dim)
            self.if_char = True
        else:
            print('can only use one char embedding (cnnChar or lstmChar)')



        self.input_dim = sum(emb_models_dim)

        if self.args.transformer:
            self.n_head = self.args.trans_head
            self.emb_dim = int((self.input_dim / self.n_head)) * self.n_head
            print('self.emb_dim', self.emb_dim)
            print('self.input_dim', self.input_dim)
            self.emb_linear = nn.Linear(in_features=self.input_dim, out_features=self.emb_dim)
            self.transEncodeLayer = TransformerEncoderLayer(d_model=self.emb_dim, nhead=self.n_head)
            self.transformer_encoder = TransformerEncoder(encoder_layer=self.transEncodeLayer, num_layers=6)
            self.input_dim = self.emb_dim

            self.transClassify_lin = nn.Linear(in_features=self.emb_dim, out_features=class_num + 1)


        if rnn_type == 'GRU':
            self.birnn_layer = LayerBiGRU(args=args,
                                          input_dim=self.input_dim,
                                          hidden_dim=rnn_hidden_dim,
                                          gpu=gpu)
        elif rnn_type == 'LSTM':
            self.birnn_layer = LayerBiLSTM(args=args,
                                           input_dim=self.input_dim,
                                           hidden_dim=rnn_hidden_dim,
                                           gpu=gpu)
        elif rnn_type == 'Vanilla':
            self.birnn_layer = LayerBiVanilla(args=args,
                                              input_dim=self.input_dim,
                                              hidden_dim=rnn_hidden_dim,
                                           gpu=gpu)
        elif self.rnn_type == 'SATN':
            self.birnn_layer = LayerSelfAttn(
                args=args,
                input_dim=self.input_dim,
                hidden_dim=rnn_hidden_dim,
                gpu=gpu)
        elif self.rnn_type == 'WCNN':
            self.birnn_layer = LayerWCNN(
                args=args,
                input_dim=self.input_dim,
                hidden_dim=rnn_hidden_dim,
                cnn_layer=args.wcnn_layer,
                # wcnn_hidden_dim =args.wcnn_hidden_dim,
                gpu=gpu)
        else:
            raise ValueError('Unknown rnn_type = %s, must be either "LSTM" or "GRU"')

        self.lin_layer = nn.Linear(in_features=self.birnn_layer.output_dim, out_features=class_num + 2)
        self.crf_layer = LayerCRF(gpu, states_num=class_num + 2, pad_idx=tag_seq_indexer.pad_idx, sos_idx=class_num + 1,
                                  tag_seq_indexer=tag_seq_indexer)
        self.softmax = nn.Softmax(dim=2)
        if gpu >= 0:
            self.cuda(device=self.gpu)


    def _forward_birnn(self, word_sequences,input_sequence):
        z = torch.tensor([0])
        if self.if_word==True and self.if_char==False:
            z_word_embed = self.word_embeddings_layer(word_sequences)
            z = self.dropout(z_word_embed)
        elif self.args.if_elmo == True and self.if_word==False:
            z_elmo = self.elmo_embeddings_layer(input_sequence)
            # z_elmo = self.W_elmo(z_elmo)
            z = self.dropout(z_elmo)
            print('using elmo emb...')

        # if self.args.if_bert==True and self.if_word==False:
        #     # z_bert = self.bert_embeddings_layer(word_sequences,'first')
        #     z_bert = self.bert_embeddings_layer(word_sequences, input_bert)
        #     # z_bert = self.W_bert(z_bert)
        #     z = self.dropout(z_bert)
        #     print('using bert emb...')
        # if self.args.if_flair ==True and self.if_word==False:
        #     z_flair = self.flair_embeddings_layer(word_sequences)
        #     z_flair = self.W_flair(z_flair.cuda())
        #     z = self.dropout(z_flair)
        #     print('using flair emb...')
        # if self.args.if_elmo ==True and self.if_word==False:
        #     z_elmo = self.elmo_embeddings_layer(word_sequences)
        #     # z_elmo = self.W_elmo(z_elmo)
        #     z = self.dropout(z_elmo)
        #     print('using elmo emb...')
        # if self.if_word==True and self.if_char==True:
        #     z_word_embed = self.word_embeddings_layer(word_sequences)
        #     z_word_embed_d = self.dropout(z_word_embed)
        #     z_char_embed = self.char_embeddings_layer(word_sequences)
        #     z_char_embed_d = self.dropout(z_char_embed)
        #     z_char_cnn = self.char_layer(z_char_embed_d)
        #     z = torch.cat((z_word_embed_d, z_char_cnn), dim=2)
        # if self.if_word == False and self.if_char == True:
        #     z_char_embed = self.char_embeddings_layer(word_sequences)
        #     z_char_embed_d = self.dropout(z_char_embed)
        #     z = self.char_layer(z_char_embed_d)
        # if self.if_word==True and self.args.if_bert==True and self.args.if_elmo==False:
        #     z_word_embed = self.word_embeddings_layer(word_sequences)
        #     z_word_embed_d = self.dropout(z_word_embed)
        #     z_bert = self.bert_embeddings_layer(word_sequences,input_bert)
        #     z_bert_d = self.dropout(z_bert)
        #     z = torch.cat((z_word_embed_d, z_bert_d), dim=2)
        # if self.if_word==True and self.args.if_elmo==True:
        #     z_word_embed = self.word_embeddings_layer(word_sequences)
        #     z_word_embed_d = self.dropout(z_word_embed)
        #     z_elmo = self.elmo_embeddings_layer(word_sequences)
        #     z_elmo_d = self.dropout(z_elmo)
        #     z = torch.cat((z_word_embed_d, z_elmo_d), dim=2)
        # if self.if_word==True and self.args.if_flair==True:
        #     z_word_embed = self.word_embeddings_layer(word_sequences)
        #     z_word_embed_d = self.dropout(z_word_embed)
        #     z_flair = self.flair_embeddings_layer(word_sequences)
        #     z_flair = self.W_flair(z_flair.cuda())
        #     z_flair_d = self.dropout(z_flair)
        #     z = torch.cat((z_word_embed_d, z_flair_d), dim=2)
        # if self.if_word==False and self.args.if_elmo==True and self.args.if_bert==True:
        #     z_elmo = self.bert_embeddings_layer(word_sequences)
        #     z_elmo_d = self.dropout(z_elmo)
        #     z_bert = self.elmo_embeddings_layer(word_sequences)
        #     z_bert_d = self.dropout(z_bert)
        #     z = torch.cat((z_elmo_d,z_bert_d), dim=2)
        # if self.if_word==True and self.args.if_elmo==True and self.args.if_bert==True:
        #     z_word_embed = self.word_embeddings_layer(word_sequences)
        #     z_word_embed_d = self.dropout(z_word_embed)
        #     z_bert = self.bert_embeddings_layer(word_sequences,input_bert)
        #     z_bert = self.W_bert(z_bert)
        #     z_bert_d = self.dropout(z_bert)
        #
        #     z_elmo = self.elmo_embeddings_layer(word_sequences)
        #     z_elmo =  self.W_elmo(z_elmo)
        #     z_elmo_d = self.dropout(z_elmo)
        #     z = torch.cat((z_word_embed_d,z_bert_d,z_elmo_d), dim=2)
        # print("input_sequence",input_sequence)
        mask = self.get_mask_from_word_sequences(input_sequence)
        # print('z:',z)
        if self.args.transformer:
            z = self.emb_linear(z)
            z.permute(1, 0, 2)
            z = self.transformer_encoder(z)

            if not self.args.transformer_useSentEncode:
                features_rnn_compressed = self.apply_mask(self.transClassify_lin(z), mask)

            else:
                rnn_output_h = self.apply_mask(self.birnn_layer(z, mask), mask)
                features_rnn_compressed = self.lin_layer(rnn_output_h)
        else:
            # print('z.shape',z.shape)
            # print('mask.shape', mask.shape)
            rnn_output_h = self.apply_mask(self.birnn_layer(z, mask), mask)
            features_rnn_compressed = self.lin_layer(rnn_output_h)
        # print('features_rnn_compressedsed',features_rnn_compressed)

        # rnn_output_h = self.apply_mask(self.birnn_layer(z, mask), mask)
        # features_rnn_compressed = self.lin_layer(rnn_output_h)
        return self.apply_mask(features_rnn_compressed, mask)


        # mask = self.get_mask_from_word_sequences(word_sequences)
        # rnn_output_h = self.apply_mask(self.birnn_layer(z,mask), mask)
        # features_rnn_compressed = self.lin_layer(rnn_output_h)
        # return self.apply_mask(features_rnn_compressed, mask)

        # z_word_embed = self.word_embeddings_layer(word_sequences)
        # z_word_embed_d = self.dropout(z_word_embed)
        # z_char_embed = self.char_embeddings_layer(word_sequences)
        # z_char_embed_d = self.dropout(z_char_embed)
        # z_char_cnn = self.char_cnn_layer(z_char_embed_d)
        # z = torch.cat((z_word_embed_d, z_char_cnn), dim=2)
        # rnn_output_h = self.apply_mask(self.birnn_layer(z, mask), mask)
        # features_rnn_compressed = self.lin_layer(rnn_output_h)
        # return self.apply_mask(features_rnn_compressed, mask)

    def get_loss(self, word_sequences_train_batch,input_sequence,tag_sequences_train_batch):
        targets_tensor_train_batch = self.tag_seq_indexer.items2tensor(tag_sequences_train_batch)
        features_rnn = self._forward_birnn(word_sequences_train_batch,input_sequence) # batch_num x max_seq_len x class_num
        mask = self.get_mask_from_word_sequences(input_sequence)  # batch_num x max_seq_len
        numerator = self.crf_layer.numerator(features_rnn, targets_tensor_train_batch, mask)
        denominator = self.crf_layer.denominator(features_rnn, mask)
        nll_loss = -torch.mean(numerator - denominator)
        return nll_loss

    def predict_idx_from_words(self, word_sequences,input_sequence,no=-1):
        self.eval()
        features_rnn_compressed_masked  = self._forward_birnn(word_sequences,input_sequence)
        mask = self.get_mask_from_word_sequences(input_sequence)
        idx_sequences = self.crf_layer.decode_viterbi(features_rnn_compressed_masked, mask)
        return idx_sequences


    def predict_tags_from_words(self, word_sequences, input_sequence,batch_size=-1):
        if batch_size == -1:
            batch_size = self.batch_size
        print('\n')
        batch_num = math.floor(len(word_sequences) / batch_size)
        if len(word_sequences) > 0 and len(word_sequences) < batch_size:
            batch_num = 1
        output_tag_sequences = list()
        for n in range(batch_num):
            i = n*batch_size
            if n < batch_num - 1:
                j = (n + 1)*batch_size
            else:
                j = len(word_sequences)
            if batch_size == 1:
                curr_output_idx = self.predict_idx_from_words(word_sequences[i:j],input_sequence[i:j], n)
            else:
                curr_output_idx = self.predict_idx_from_words(word_sequences[i:j],input_sequence[i:j], -1)
            curr_output_tag_sequences = self.tag_seq_indexer.idx2items(curr_output_idx)
            output_tag_sequences.extend(curr_output_tag_sequences)
            print('\r++ predicting, batch %d/%d (%1.2f%%).' % (n + 1, batch_num, math.ceil(n * 100.0 / batch_num)),
                  end='', flush=True)
        return output_tag_sequences