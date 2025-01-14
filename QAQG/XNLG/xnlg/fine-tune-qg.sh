#!/usr/bin/env bash
source ../ENV/bin/activate
module load         java/1.8.0_192
ssh beluga4 -L 12345:localhost:12345 -N -f
#../dump/xqg/4790453/best_en-en_Bleu_4.pth
python xnlg-ft.py --exp_name xqg --dump_path ../dump --model_path ../data/model/en-fr-zh_valid-en-fr.pth  --data_path ../data/processed/XNLG --transfer_tasks XQG --optimizer adam,lr=0.000005 --batch_size 16 --n_epochs 200 --epoch_size 4000 --max_len_q 256 --max_len_a 20 --max_len_e 230 --max_vocab 95000 --train_layers 1,10                     --vocab_path ../data/xqg-decoding-vocab --decode_with_vocab True                 --decode_vocab_sizes 95000,95000,95000 --n_enc_layers 10 --n_dec_layers 6 --beam_size 3 --ds_name xqg --train_directions fr-fr --eval_directions fr-fr,en-en --remote_debug True
