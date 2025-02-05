from random import randint, shuffle, choice
from random import random as rand
import math
import torch

from loader_utils import get_random_word, batch_list_to_batch_tensors, Pipeline


# Input file format :
# 1. One sentence per line. These should ideally be actual sentences,
#    not entire paragraphs or arbitrary spans of text. (Because we use
#    the sentence boundaries for the "next sentence prediction" task).
# 2. Blank lines between documents. Document boundaries are needed
#    so that the "next sentence prediction" task doesn't span between documents.
def split_list(iterable, splitter):
    thresh = splitter
    lst = []
    outer = []
    for value in iterable:
        if thresh is None:
            lst.append(value)
            continue
        elif value == thresh:
            if lst: outer.append(lst)
            lst = [value]
        else:
            lst.append(value)

    if lst: outer.append(lst)
    return outer


def truncate_tokens_pair(tokens_a, tokens_b, max_len, max_len_a=0, max_len_b=0, trunc_seg=None,
                         always_truncate_tail=False):
    num_truncated_a = [0, 0]
    num_truncated_b = [0, 0]
    while True:
        if len(tokens_a) + len(tokens_b) <= max_len:
            break
        if (max_len_a > 0) and len(tokens_a) > max_len_a:
            trunc_tokens = tokens_a
            num_truncated = num_truncated_a
        elif (max_len_b > 0) and len(tokens_b) > max_len_b:
            trunc_tokens = tokens_b
            num_truncated = num_truncated_b
        elif trunc_seg:
            # truncate the specified segment
            if trunc_seg == 'a':
                trunc_tokens = tokens_a
                num_truncated = num_truncated_a
            else:
                trunc_tokens = tokens_b
                num_truncated = num_truncated_b
        else:
            # truncate the longer segment
            if len(tokens_a) > len(tokens_b):
                trunc_tokens = tokens_a
                num_truncated = num_truncated_a
            else:
                trunc_tokens = tokens_b
                num_truncated = num_truncated_b
        # whether always truncate source sequences
        if (not always_truncate_tail) and (rand() < 0.5):
            del trunc_tokens[0]
            num_truncated[0] += 1
        else:
            trunc_tokens.pop()
            num_truncated[1] += 1
    return num_truncated_a, num_truncated_b


class Seq2SeqDataset(torch.utils.data.Dataset):
    """ Load sentence pair (sequential or random order) from corpus """

    def __init__(self, file_src, file_tgt, batch_size, tokenizer, max_len, file_oracle=None, short_sampling_prob=0.1,
                 sent_reverse_order=False, bi_uni_pipeline=[]):
        super().__init__()
        self.tokenizer = tokenizer  # tokenize function
        self.max_len = max_len  # maximum length of tokens
        self.short_sampling_prob = short_sampling_prob

        self.bi_uni_pipeline = bi_uni_pipeline
        self.batch_size = batch_size

        # read the file into memory
        self.ex_list = []
        if file_oracle is None:
            with open(file_src, "r", encoding='utf-8') as f_src, open(file_tgt, "r", encoding='utf-8') as f_tgt:
                for src, tgt in zip(f_src, f_tgt):
                    src_tk = tokenizer.tokenize(src.strip())
                    tgt_tk = tokenizer.tokenize(tgt.strip())
                    assert len(src_tk) > 0
                    assert len(tgt_tk) > 0
                    self.ex_list.append((src_tk, tgt_tk))
        else:
            with open(file_src, "r", encoding='utf-8') as f_src, \
                    open(file_tgt, "r", encoding='utf-8') as f_tgt, \
                    open(file_oracle, "r", encoding='utf-8') as f_orc:
                for src, tgt, orc in zip(f_src, f_tgt, f_orc):
                    src_tk = tokenizer.tokenize(src.strip())
                    tgt_tk = tokenizer.tokenize(tgt.strip())
                    s_st, labl = orc.split('\t')
                    s_st = [int(x) for x in s_st.split()]
                    labl = [int(x) for x in labl.split()]
                    self.ex_list.append((src_tk, tgt_tk, s_st, labl))
        print('Load {0} documents'.format(len(self.ex_list)))

    def __len__(self):
        return len(self.ex_list)

    def __getitem__(self, idx):
        instance = self.ex_list[idx]
        proc = choice(self.bi_uni_pipeline)
        instance = proc(instance)
        return instance

    def __iter__(self):  # iterator to load data
        for __ in range(math.ceil(len(self.ex_list) / float(self.batch_size))):
            batch = []
            for __ in range(self.batch_size):
                idx = randint(0, len(self.ex_list) - 1)
                batch.append(self.__getitem__(idx))
            # To Tensor
            yield batch_list_to_batch_tensors(batch)


class Preprocess4Seq2seq(Pipeline):
    """ Pre-processing steps for pretraining transformer """

    def __init__(self, max_pred, mask_prob, vocab_words, indexer, max_len=512, skipgram_prb=0, skipgram_size=0,
                 block_mask=False, mask_whole_word=False, new_segment_ids=False, truncate_config={},
                 mask_source_words=False, mode="s2s", has_oracle=False, num_qkv=0, s2s_special_token=False,
                 s2s_add_segment=False, s2s_share_segment=False, pos_shift=False):
        super().__init__()
        self.max_len = max_len
        self.max_pred = max_pred  # max tokens of prediction
        self.mask_prob = mask_prob  # masking probability
        self.vocab_words = vocab_words  # vocabulary (sub)words
        self.indexer = indexer  # function from token to token index
        self.max_len = max_len
        self._tril_matrix = torch.tril(torch.ones(
            (max_len, max_len), dtype=torch.long))
        self.skipgram_prb = skipgram_prb
        self.skipgram_size = skipgram_size
        self.mask_whole_word = mask_whole_word
        self.new_segment_ids = new_segment_ids
        self.always_truncate_tail = truncate_config.get(
            'always_truncate_tail', False)
        self.max_len_a = truncate_config.get('max_len_a', None)
        self.max_len_b = truncate_config.get('max_len_b', None)
        self.trunc_seg = truncate_config.get('trunc_seg', None)
        self.task_idx = 3  # relax projection layer for different tasks
        self.mask_source_words = mask_source_words
        assert mode in (
        "s2s", "l2r", "2in1", "double_s2s", "a2q", "q2a", "triple_s2s", "c2q", "c2a", "Q-AQ", "A-QA", "double_joint")
        self.mode = mode
        self.has_oracle = has_oracle
        self.num_qkv = num_qkv
        self.s2s_special_token = s2s_special_token
        self.s2s_add_segment = s2s_add_segment
        self.s2s_share_segment = s2s_share_segment
        self.pos_shift = pos_shift

    def __call__(self, instance, mode=None):

        if mode is not None:
            mode = mode
        else:
            mode = self.mode

        tokens_a, tokens_b = instance[:2]

        mtuple = split_list(instance[0], "[SEP]")
        tokens_cxt = mtuple[0]
        tokens_ans = mtuple[1][1:]
        tokens_qtn = instance[1]

        if mode == "Q-AQ":
            result1 = self.__call__(instance, mode="c2q")  # c,a to questions
            result2 = self.__call__(instance, mode="q2a")
            return (result1, result2)

        if mode == "A-QA":
            result1 = self.__call__(instance, mode="c2a")  # c,a to questions
            result2 = self.__call__(instance, mode="a2q")

            return (result1, result2)

        if mode == "double_joint":
            result1 = self.__call__(instance, mode="Q-AQ")  # c,a to questions
            result2 = self.__call__(instance, mode="A-QA")
            result = result1 + result2
            return result

        if mode == "double_s2s":
            result1 = self.__call__(instance, mode="a2q")  # c,a to questions
            result2 = self.__call__(instance, mode="q2a")

            return (result1, result2)

        if mode == "triple_s2s":
            results = self.__call__(instance, mode="double_s2s")
            result3 = self.__call__(instance, mode="2in1")

            return (results[0], results[1], result3)

        if mode == "a2q":
            tokens_a = tokens_cxt + ['SEP'] + tokens_ans
            tokens_b = tokens_qtn

        if mode == "q2a":
            tokens_a = tokens_cxt + ['SEP'] + tokens_qtn
            tokens_b = tokens_ans
        if mode == "c2q":
            tokens_a = tokens_cxt
            tokens_b = tokens_qtn

        if mode == "c2a":
            tokens_a = tokens_cxt
            tokens_b = tokens_ans

        if mode == "2in1":
            tokens_a = tokens_cxt
            tokens_m = tokens_ans
            tokens_b = tokens_ans + ['SEP'] + tokens_qtn

        if self.pos_shift:
            tokens_a = tokens_cxt + ['SEP'] + tokens_ans
            tokens_b = ['[S2S_SOS]'] + tokens_qtn

        # -3  for special tokens [CLS], [SEP], [SEP]
        num_truncated_a, _ = truncate_tokens_pair(tokens_a, tokens_b, self.max_len - 3, max_len_a=self.max_len_a,
                                                  max_len_b=self.max_len_b, trunc_seg=self.trunc_seg,
                                                  always_truncate_tail=self.always_truncate_tail)

        # Add Special Tokens
        if self.s2s_special_token:
            tokens = ['[S2S_CLS]'] + tokens_a + \
                     ['[S2S_SEP]'] + tokens_b + ['[SEP]']
        elif mode == '2in1':
            tokens = ['[SEP_0]'] + tokens_a + ['[SEP_3]'] + tokens_b + ['[SEP]']
        elif mode == 'a2q':
            tokens = ['[SEP_1]'] + tokens_cxt + ['[SEP_3]'] + tokens_ans + ['[SEP_4]'] + tokens_qtn + ['[SEP]']
        elif mode == 'q2a':
            tokens = ['[SEP_2]'] + tokens_cxt + ['[SEP_3]'] + tokens_qtn + ['[SEP_5]'] + tokens_ans + ['[SEP]']
        elif mode == 'c2q':
            tokens = ['[SEP_6]'] + tokens_cxt + ['[SEP_3]'] + tokens_qtn + ['[SEP]']
        elif mode == 'c2a':
            tokens = ['[SEP_7]'] + tokens_cxt + ['[SEP_3]'] + tokens_ans + ['[SEP]']
        else:
            tokens = ['[CLS]'] + tokens_a + ['[SEP]'] + tokens_b + ['[SEP]']

        if self.new_segment_ids:
            if mode == "s2s" or mode == "2in1":
                if self.s2s_add_segment:
                    if self.s2s_share_segment:
                        segment_ids = [0] + [1] * \
                                      (len(tokens_a) + 1) + [5] * (len(tokens_b) + 1)
                    else:
                        segment_ids = [4] + [6] * \
                                      (len(tokens_a) + 1) + [5] * (len(tokens_b) + 1)
                else:
                    segment_ids = [4] * (len(tokens_a) + 2) + \
                                  [5] * (len(tokens_b) + 1)
            elif mode == "a2q":
                segment_ids = [1] * (len(tokens_cxt) + 2) + [2] * (len(tokens_ans) + 1) + [3] * (len(tokens_qtn) + 1)
            elif mode == 'q2a':
                segment_ids = [1] * (len(tokens_cxt) + 2) + [3] * (len(tokens_qtn) + 1) + [2] * (len(tokens_ans) + 1)
            elif mode == "c2q":
                segment_ids = [1] * (len(tokens_cxt) + 2) + [3] * (len(tokens_qtn) + 1)
            elif mode == "c2a":
                segment_ids = [1] * (len(tokens_cxt) + 2) + [2] * (len(tokens_ans) + 1)
            else:
                segment_ids = [2] * (len(tokens))
        else:
            segment_ids = [0] * (len(tokens_a) + 2) + [1] * (len(tokens_b) + 1)

        if self.pos_shift:
            n_pred = min(self.max_pred, len(tokens_b))
            masked_pos = [len(tokens_a) + 2 + i for i in range(len(tokens_b))]
            masked_weights = [1] * n_pred
            masked_ids = self.indexer(tokens_b[1:] + ['[SEP]', '[SEP_4]', '[SEP_5]'])
        else:
            # For masked Language Models
            # the number of prediction is sometimes less than max_pred when sequence is short
            # the length of prediction of question should not

            effective_length = len(tokens_b)

            if self.mask_source_words:
                effective_length += len(tokens_a)

            n_pred = min(self.max_pred, max(
                1, int(round(effective_length * self.mask_prob))))
            # candidate positions of masked tokens
            cand_pos = []
            special_pos = set()
            for i, tk in enumerate(tokens):
                # only mask tokens_b (target sequence)
                # we will mask [SEP] as an ending symbol
                if (i >= len(tokens_a) + 2) and (tk != '[CLS]'):
                    cand_pos.append(i)
                elif self.mask_source_words and (i < len(tokens_a) + 2) and (tk != '[CLS]') and (
                        not tk.startswith('[SEP')):
                    cand_pos.append(i)
                else:
                    special_pos.add(i)
            shuffle(cand_pos)

            masked_pos = set()
            max_cand_pos = max(cand_pos)
            for pos in cand_pos:
                if len(masked_pos) >= n_pred:
                    break
                if pos in masked_pos:
                    continue

                def _expand_whole_word(st, end):
                    new_st, new_end = st, end
                    while (new_st >= 0) and tokens[new_st].startswith('##'):
                        new_st -= 1
                    while (new_end < len(tokens)) and tokens[new_end].startswith('##'):
                        new_end += 1
                    return new_st, new_end

                if (self.skipgram_prb > 0) and (self.skipgram_size >= 2) and (rand() < self.skipgram_prb):
                    # ngram
                    cur_skipgram_size = randint(2, self.skipgram_size)
                    if self.mask_whole_word:
                        st_pos, end_pos = _expand_whole_word(
                            pos, pos + cur_skipgram_size)
                    else:
                        st_pos, end_pos = pos, pos + cur_skipgram_size
                else:
                    # directly mask
                    if self.mask_whole_word:
                        st_pos, end_pos = _expand_whole_word(pos, pos + 1)
                    else:
                        st_pos, end_pos = pos, pos + 1

                for mp in range(st_pos, end_pos):
                    if (0 < mp <= max_cand_pos) and (mp not in special_pos):
                        masked_pos.add(mp)
                    else:
                        break

            masked_pos = list(masked_pos)
            if len(masked_pos) > n_pred:
                shuffle(masked_pos)
                masked_pos = masked_pos[:n_pred]

            masked_tokens = [tokens[pos] for pos in masked_pos]
            for pos in masked_pos:
                if rand() < 0.8:  # 80%
                    tokens[pos] = '[MASK]'
                elif rand() < 0.5:  # 10%
                    tokens[pos] = get_random_word(self.vocab_words)
            # when n_pred < max_pred, we only calculate loss within n_pred
            masked_weights = [1] * len(masked_tokens)

            # Token Indexing
            masked_ids = self.indexer(masked_tokens)
        # Token Indexing
        input_ids = self.indexer(tokens)

        # Zero Padding
        n_pad = self.max_len - len(input_ids)
        input_ids.extend([0] * n_pad)
        segment_ids.extend([0] * n_pad)

        if self.num_qkv > 1:
            mask_qkv = [0] * (len(tokens_a) + 2) + [1] * (len(tokens_b) + 1)
            mask_qkv.extend([0] * n_pad)
        else:
            mask_qkv = None

        input_mask = torch.zeros(self.max_len, self.max_len, dtype=torch.long)
        if mode in ["s2s", "a2q", "q2a", "c2a", "c2q"]:
            input_mask[:, :len(tokens_a) + 2].fill_(1)
            second_st, second_end = len(tokens_a) + 2, len(tokens_a) + len(tokens_b) + 3
            input_mask[second_st:second_end, second_st:second_end].copy_(
                self._tril_matrix[:second_end - second_st, :second_end - second_st])
        elif mode == "2in1":
            input_mask[:, :len(tokens_a) + 2].fill_(1)
            second_st, second_end = len(tokens_a) + 2, len(tokens_a) + len(tokens_b) + 3

            mid_st, mid_end = len(tokens_a) + 2, len(tokens_a) + len(tokens_m) + 3

            input_mask[second_st:second_end, second_st:second_end].copy_(
                self._tril_matrix[:second_end - second_st, :second_end - second_st])
            input_mask[mid_st:mid_end, mid_end:].fill_(1)

        else:
            st, end = 0, len(tokens_a) + len(tokens_b) + 3
            input_mask[st:end, st:end].copy_(self._tril_matrix[:end, :end])

        # Zero Padding for masked target
        if self.max_pred > n_pred:
            n_pad = self.max_pred - n_pred
            if masked_ids is not None:
                masked_ids.extend([0] * n_pad)
            if masked_pos is not None:
                masked_pos.extend([0] * n_pad)
            if masked_weights is not None:
                masked_weights.extend([0] * n_pad)

        oracle_pos = None
        oracle_weights = None
        oracle_labels = None
        if self.has_oracle:
            s_st, labls = instance[2:]
            oracle_pos = []
            oracle_labels = []
            for st, lb in zip(s_st, labls):
                st = st - num_truncated_a[0]
                if st > 0 and st < len(tokens_a):
                    oracle_pos.append(st)
                    oracle_labels.append(lb)
            oracle_pos = oracle_pos[:20]
            oracle_labels = oracle_labels[:20]
            oracle_weights = [1] * len(oracle_pos)
            if len(oracle_pos) < 20:
                x_pad = 20 - len(oracle_pos)
                oracle_pos.extend([0] * x_pad)
                oracle_labels.extend([0] * x_pad)
                oracle_weights.extend([0] * x_pad)

            return (input_ids, segment_ids, input_mask, mask_qkv, masked_ids,
                    masked_pos, masked_weights, -1, self.task_idx,
                    oracle_pos, oracle_weights, oracle_labels)

        return (input_ids, segment_ids, input_mask, mask_qkv, masked_ids, masked_pos, masked_weights, -1, self.task_idx)


class Preprocess4Seq2seqDecoder(Pipeline):
    """ Pre-processing steps for pretraining transformer """

    def __init__(self, vocab_words, indexer, max_len=512, max_tgt_length=128, new_segment_ids=False, mode="s2s",
                 num_qkv=0, s2s_special_token=False, s2s_add_segment=False, s2s_share_segment=False, pos_shift=False):
        super().__init__()
        self.max_len = max_len
        self.vocab_words = vocab_words  # vocabulary (sub)words
        self.indexer = indexer  # function from token to token index
        self.max_len = max_len
        self._tril_matrix = torch.tril(torch.ones(
            (max_len, max_len), dtype=torch.long))
        self.new_segment_ids = new_segment_ids
        self.task_idx = 3  # relax projection layer for different tasks
        assert mode in ("s2s", "l2r", "2in1", "a2q", "q2a", "c2q", "c2a")
        self.mode = mode
        self.max_tgt_length = max_tgt_length
        self.num_qkv = num_qkv
        self.s2s_special_token = s2s_special_token
        self.s2s_add_segment = s2s_add_segment
        self.s2s_share_segment = s2s_share_segment
        self.pos_shift = pos_shift

    def __call__(self, instance, mode=None):
        tokens_a, max_a_len = instance

        if mode is not None:
            mode = mode
        else:
            mode = self.mode

        if mode == "2in1":
            mtuple = split_list(tokens_a, "[SEP]")
            tokens_a = mtuple[0]

        if mode == "a2q" or mode == "c2q":
            mtuple = split_list(tokens_a, "[SEP]")
            tokens_cxt = mtuple[0]

            if len(mtuple) == 2:
                tokens_ans = mtuple[1][1:]
            else:
                tokens_ans = [""]

        if mode == "q2a" or mode == "c2a":
            mtuple = split_list(tokens_a, "[SEP]")
            tokens_cxt = mtuple[0]
            tokens_qtn = mtuple[1][1:]

        # Add Special Tokens
        if self.s2s_special_token:
            padded_tokens_a = ['[S2S_CLS]'] + tokens_a + ['[S2S_SEP]']
        elif mode == "2in1":
            padded_tokens_a = ['[SEP_0]'] + tokens_a + ['[SEP_3]']
        elif mode == "a2q":
            padded_tokens_a = ['[SEP_1]'] + tokens_cxt + ['[SEP_3]'] + tokens_ans + ['[SEP_4]']
        elif mode == "q2a":
            padded_tokens_a = ['[SEP_2]'] + tokens_cxt + ['[SEP_3]'] + tokens_qtn + ['[SEP_5]']
        elif mode == 'c2q':
            padded_tokens_a = ['[SEP_6]'] + tokens_cxt + ['[SEP_3]']
        elif mode == 'c2a':
            padded_tokens_a = ['[SEP_7]'] + tokens_cxt + ['[SEP_3]']
        else:
            padded_tokens_a = ['[CLS]'] + tokens_a + ['[SEP]']

        assert len(padded_tokens_a) <= max_a_len + 2
        if max_a_len + 2 > len(padded_tokens_a):
            padded_tokens_a += ['[PAD]'] * \
                               (max_a_len + 2 - len(padded_tokens_a))
        assert len(padded_tokens_a) == max_a_len + 2
        max_len_in_batch = min(self.max_tgt_length +
                               max_a_len + 2, self.max_len)
        tokens = padded_tokens_a
        if self.new_segment_ids:
            if mode == "s2s" or mode == "2in1":
                _enc_seg1 = 0 if self.s2s_share_segment else 4
                if self.s2s_add_segment:
                    if self.s2s_share_segment:
                        segment_ids = [0] + [1] * (len(padded_tokens_a) - 1) + [5] * (
                                max_len_in_batch - len(padded_tokens_a))
                    else:
                        segment_ids = [4] + [6] * (len(padded_tokens_a) - 1) + [5] * (
                                max_len_in_batch - len(padded_tokens_a))  # default s2s mode

                else:
                    segment_ids = [4] * (len(padded_tokens_a)) + \
                                  [5] * (max_len_in_batch - len(padded_tokens_a))
            elif mode == "a2q":
                segment_ids = [1] * (len(tokens_cxt) + 2) + [2] * (len(tokens_ans) + 1) + [3] * (
                        max_len_in_batch - len(tokens_ans + tokens_cxt) - 3)
            elif mode == 'q2a':
                segment_ids = [1] * (len(tokens_cxt) + 2) + [3] * (len(tokens_qtn) + 1) + [2] * (
                        max_len_in_batch - len(tokens_cxt + tokens_qtn) - 3)
            elif mode == 'c2q':
                segment_ids = [1] + [1] * (len(padded_tokens_a) - 1) + [3] * (
                        max_len_in_batch - len(padded_tokens_a))
            elif mode == 'c2a':
                segment_ids = [1] + [1] * (len(padded_tokens_a) - 1) + [2] * (
                        max_len_in_batch - len(padded_tokens_a))
            else:
                segment_ids = [2] * max_len_in_batch
        else:
            segment_ids = [0] * (len(padded_tokens_a)) \
                          + [1] * (max_len_in_batch - len(padded_tokens_a))

        if self.num_qkv > 1:
            mask_qkv = [0] * (len(padded_tokens_a)) + [1] * \
                       (max_len_in_batch - len(padded_tokens_a))
        else:
            mask_qkv = None

        position_ids = []
        for i in range(len(tokens_a) + 2):
            position_ids.append(i)
        for i in range(len(tokens_a) + 2, max_a_len + 2):
            position_ids.append(0)
        for i in range(max_a_len + 2, max_len_in_batch):
            position_ids.append(i - (max_a_len + 2) + len(tokens_a) + 2)

        # Token Indexing
        input_ids = self.indexer(tokens)

        # Zero Padding
        input_mask = torch.zeros(
            max_len_in_batch, max_len_in_batch, dtype=torch.long)
        if mode == "s2s" or mode == "2in1" or mode == "a2q" or mode == "q2a" or mode == "c2q" or mode == "c2a":
            input_mask[:, :len(tokens_a) + 2].fill_(1)
        else:
            st, end = 0, len(tokens_a) + 2
            input_mask[st:end, st:end].copy_(
                self._tril_matrix[:end, :end])
            input_mask[end:, :len(tokens_a) + 2].fill_(1)
        second_st, second_end = len(padded_tokens_a), max_len_in_batch

        input_mask[second_st:second_end, second_st:second_end].copy_(
            self._tril_matrix[:second_end - second_st, :second_end - second_st])

        return (input_ids, segment_ids, position_ids, input_mask, mask_qkv, self.task_idx)

# preprocess for squad QA head with qg generation
# class Preprocess4Squad(Pipeline):
