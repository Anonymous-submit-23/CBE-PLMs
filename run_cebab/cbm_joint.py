import torch
import transformers
from gensim.models import FastText
from torch.optim.lr_scheduler import StepLR
from transformers import RobertaTokenizer, RobertaModel,BertModel, BertTokenizer,GPT2Model, GPT2Tokenizer
from torch.utils.data import DataLoader, Dataset
from torch.utils.data.sampler import Sampler
import torch.nn.functional as F
from datasets import load_dataset
from tqdm import tqdm
from sklearn.metrics import f1_score
import numpy as np
import pandas as pd
import os 
from cbm_template_models import MLP, FC
from cbm_models import ModelXtoC_function, ModelCtoY_function,ModelXtoCtoY_function

# Enable concept or not
mode = 'joint'

# Define the paths to the dataset and pretrained model
# model_name = "microsoft/deberta-base"
model_name = 'lstm' # 'bert-base-uncased' / 'roberta-base' / 'gpt2' / 'lstm'

# Define the maximum sequence length and batch size
max_len = 128
batch_size = 8
lambda_XtoC = 0.5  # lambda > 0
is_aux_logits = False
num_labels = 5  #label的个数
num_epochs = 10                
num_each_concept_classes = 3  #每个concept有几个类


# Load the tokenizer and pretrained model
if model_name == 'roberta-base':
    tokenizer = RobertaTokenizer.from_pretrained(model_name)
    model = RobertaModel.from_pretrained(model_name)
elif model_name == 'bert-base-uncased':
    tokenizer = BertTokenizer.from_pretrained(model_name)
    model = BertModel.from_pretrained(model_name)
elif model_name == 'gpt2':
    # class GPT2Classifier(torch.nn.Module):
    #     def __init__(self, gpt2_model):
    #         super().__init__()
    #         self.gpt2_model = gpt2_model
    #     def forward(self, input_ids, attention_mask):
    #         outputs = self.gpt2_model(input_ids=input_ids, attention_mask=attention_mask)
    #         last_hidden_state = outputs.last_hidden_state.mean(1)
    #         return last_hidden_state
    model = GPT2Model.from_pretrained(model_name)
    tokenizer = GPT2Tokenizer.from_pretrained(model_name)
    tokenizer.pad_token = tokenizer.eos_token
    # Initialize the classification model
    # model = GPT2Classifier(model)   
elif model_name == 'lstm':
    fasttext_model = FastText.load_fasttext_format('./fasttext/cc.en.300.bin')
    tokenizer = BertTokenizer.from_pretrained('bert-base-uncased')

    class BiLSTMWithDotAttention(torch.nn.Module):
        def __init__(self, vocab_size, embedding_dim, hidden_dim):
            super().__init__()
            self.embedding = torch.nn.Embedding(vocab_size, embedding_dim)
            embeddings = fasttext_model.wv.vectors
            self.embedding.weight = torch.nn.Parameter(torch.tensor(embeddings))
            self.embedding.weight.requires_grad = False
            self.lstm = torch.nn.LSTM(embedding_dim, hidden_dim, num_layers = 1, bidirectional=True, batch_first=True)
            self.classifier = torch.nn.Sequential(
                torch.nn.Linear(hidden_dim*2, hidden_dim),
                torch.nn.ReLU(),
                torch.nn.Dropout(0.2)
        )

        def forward(self, input_ids, attention_mask):
            input_lengths = attention_mask.sum(dim=1)
            embedded = self.embedding(input_ids)
            output, _ = self.lstm(embedded)
            weights = F.softmax(torch.bmm(output, output.transpose(1, 2)), dim=2)
            attention = torch.bmm(weights, output)
            logits = self.classifier(attention.mean(1))
            return logits

    model = BiLSTMWithDotAttention(len(tokenizer.vocab), 300, 128)

data_type = "aug_cebab" # "pure_cebab"/"aug_cebab"/"aug_yelp"/"aug_cebab_yelp"
# Load data
if data_type == "pure_cebab":
    num_concept_labels = 4
    train_split = "train_exclusive"
    test_split = "test"
    CEBaB = load_dataset("CEBaB/CEBaB")
elif data_type == "aug_cebab":
    num_concept_labels = 10
    train_split = "train_aug_cebab"
    test_split = "test_aug_cebab"
    CEBaB = {}
    CEBaB[train_split] = pd.read_csv("../dataset/cebab/train_cebab_new_concept_single.csv")
    CEBaB[test_split] = pd.read_csv("../dataset/cebab/test_cebab_new_concept_single.csv")
elif data_type == "aug_yelp":
    num_concept_labels = 10
    train_split = "train_aug_yelp"
    test_split = "test_aug_yelp"
    CEBaB = {}
    CEBaB[train_split] = pd.read_csv("../../dataset/yelp/train_yelp_new_concept_single.csv")
    CEBaB[test_split] = pd.read_csv("../../dataset/yelp/test_yelp_new_concept_single.csv")
elif data_type == "aug_cebab_yelp":
    num_concept_labels = 10

    train_split = "train_aug_cebab_yelp"
    test_split = "test_aug_cebab_yelp"
    train_split_cebab = pd.read_csv("../../dataset/cebab/train_cebab_new_concept_single.csv")
    test_split_cebab = pd.read_csv("../../dataset/cebab/test_cebab_new_concept_single.csv")
    train_split_yelp = pd.read_csv("../../dataset/yelp/train_yelp_new_concept_single.csv")
    test_split_yelp = pd.read_csv("../../dataset/yelp/test_yelp_new_concept_single.csv")

    CEBaB = {}
    CEBaB[train_split] = pd.concat([train_split_cebab, train_split_yelp], ignore_index=True)
    CEBaB[test_split] = pd.concat([test_split_cebab, test_split_yelp], ignore_index=True)

# Define a custom dataset class for loading the data
class MyDataset(Dataset):
    # Split = train/dev/test
    def __init__(self, split, skip_class = "no majority"):
        self.data = CEBaB[split]
        self.labels = self.data["review_majority"]
        self.text = self.data["description"]
       
        self.food_aspect = self.data["food_aspect_majority"]
        self.ambiance_aspect = self.data["ambiance_aspect_majority"]
        self.service_aspect = self.data["service_aspect_majority"]
        self.noise_aspect =self.data["noise_aspect_majority"]

        if data_type != "pure_cebab":
            # cleanliness price	location	menu variety	waiting time	waiting area	## parking	wi-fi	kids-friendly
            self.cleanliness_aspect = self.data["cleanliness"]
            self.price_aspect = self.data["price"]
            self.location_aspect = self.data["location"]
            self.menu_variety_aspect = self.data["menu variety"]
            self.waiting_time_aspect =self.data["waiting time"]
            self.waiting_area_aspect =self.data["waiting area"]

        self.map_dict = {"Negative":0, "Positive":1, "unknown":2, "":2,"no majority":2}

        self.skip_class = skip_class
        if skip_class is not None:
            self.indices = [i for i, label in enumerate(self.labels) if label != skip_class]
        else:
            self.indices = range(len(self.labels))

    def __len__(self):
        return len(self.indices)

    def __getitem__(self, index):
        text = self.text[self.indices[index]]
        label = int(self.labels[self.indices[index]]) - 1

        # gold labels
        food_concept = self.map_dict[self.food_aspect[self.indices[index]]]
        ambiance_concept = self.map_dict[self.ambiance_aspect[self.indices[index]]]
        service_concept = self.map_dict[self.service_aspect[self.indices[index]]]
        noise_concept = self.map_dict[self.noise_aspect[self.indices[index]]]
        
        if data_type != "pure_cebab":
            # noisy labels
            #cleanliness price	location	menu variety	waiting time	waiting area	## parking	wi-fi	kids-friendly
            cleanliness_concept = self.map_dict[self.cleanliness_aspect[self.indices[index]]]
            price_concept = self.map_dict[self.price_aspect[self.indices[index]]]
            location_concept = self.map_dict[self.location_aspect[self.indices[index]]]
            menu_variety_concept = self.map_dict[self.menu_variety_aspect[self.indices[index]]]
            waiting_time_concept = self.map_dict[self.waiting_time_aspect[self.indices[index]]]
            waiting_area_concept = self.map_dict[self.waiting_area_aspect[self.indices[index]]]

        if data_type != "pure_cebab":
            concept_labels = [food_concept,ambiance_concept,service_concept,noise_concept,cleanliness_concept,price_concept,location_concept,menu_variety_concept,waiting_time_concept,waiting_area_concept]
        else: 
            concept_labels = [food_concept,ambiance_concept,service_concept,noise_concept]

        encoding = tokenizer.encode_plus(
            text,
            add_special_tokens=True,
            max_length=max_len,
            truncation=True,
            padding="max_length",
            return_attention_mask=True,
            return_tensors="pt"
        )
        if data_type != "pure_cebab":
            return {
                "input_ids": encoding["input_ids"].flatten(),
                "attention_mask": encoding["attention_mask"].flatten(),
                "label": torch.tensor(label, dtype=torch.long),
                "food_concept": torch.tensor(food_concept, dtype=torch.long),
                "ambiance_concept": torch.tensor(ambiance_concept, dtype=torch.long),
                "service_concept": torch.tensor(service_concept, dtype=torch.long),
                "noise_concept": torch.tensor(noise_concept, dtype=torch.long),
                "cleanliness_concept": torch.tensor(cleanliness_concept, dtype=torch.long),
                "price_concept": torch.tensor(price_concept, dtype=torch.long),
                "location_concept": torch.tensor(location_concept, dtype=torch.long),
                "menu_variety_concept": torch.tensor(menu_variety_concept, dtype=torch.long),
                "waiting_time_concept": torch.tensor(waiting_time_concept, dtype=torch.long),
                "waiting_area_concept": torch.tensor(waiting_area_concept, dtype=torch.long),
                "concept_labels": torch.tensor(concept_labels, dtype=torch.long)
            }
        else:
            return {
                "input_ids": encoding["input_ids"].flatten(),
                "attention_mask": encoding["attention_mask"].flatten(),
                "label": torch.tensor(label, dtype=torch.long),
                "food_concept": torch.tensor(food_concept, dtype=torch.long),
                "ambiance_concept": torch.tensor(ambiance_concept, dtype=torch.long),
                "service_concept": torch.tensor(service_concept, dtype=torch.long),
                "noise_concept": torch.tensor(noise_concept, dtype=torch.long),
                "concept_labels": torch.tensor(concept_labels, dtype=torch.long)
            }


# Load the data
train_dataset = MyDataset(train_split)
# val_dataset = MyDataset('validation')
test_dataset = MyDataset(test_split)


# Define the dataloaders
train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
# val_loader = DataLoader(val_dataset, batch_size=batch_size)
test_loader = DataLoader(test_dataset, batch_size=batch_size)

#Set ModelXtoCtoY_layer
    # concept_classes 每个concept有几类；    label_classes  label的个数；  n_attributes concept的个数； n_class_attr 每个concept有几类；
if model_name == 'lstm':
    ModelXtoCtoY_layer = ModelXtoCtoY_function(concept_classes = num_each_concept_classes, label_classes = num_labels, n_attributes = num_concept_labels, bottleneck = True, expand_dim = 0, n_class_attr=num_each_concept_classes, use_relu=False, use_sigmoid=False,Lstm=True,aux_logits=is_aux_logits)
else:
    ModelXtoCtoY_layer = ModelXtoCtoY_function(concept_classes = num_each_concept_classes, label_classes = num_labels, n_attributes = num_concept_labels, bottleneck = True, expand_dim = 0, n_class_attr=num_each_concept_classes, use_relu=False, use_sigmoid=False,aux_logits=is_aux_logits)

# Set up the optimizer and loss function
# optimizer = torch.optim.AdamW(classifier.parameters(), lr=2e-5)
optimizer = torch.optim.Adam(list(model.parameters()) + list(ModelXtoCtoY_layer.parameters()), lr=1e-5)
if model_name == 'lstm':
    scheduler = StepLR(optimizer, step_size=10, gamma=0.1)
loss_fn = torch.nn.CrossEntropyLoss()

# Train the model
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
# classifier.to(device)
ModelXtoCtoY_layer.to(device)
model.to(device)

for epoch in range(num_epochs):
    predicted_concepts_train = []
    predicted_concepts_train_label = []
    ModelXtoCtoY_layer.train()
    model.train()
    
    for batch in tqdm(train_loader, desc="Training", unit="batch"):
        input_ids = batch["input_ids"].to(device)
        attention_mask = batch["attention_mask"].to(device)
        label = batch["label"].to(device)
        food_concept = batch["food_concept"].to(device)
        ambiance_concept=batch["ambiance_concept"].to(device)
        service_concept=batch["service_concept"].to(device)
        noise_concept=batch["noise_concept"].to(device)

        if data_type != "pure_cebab":
            cleanliness_concept = batch["cleanliness_concept"].to(device)
            price_concept = batch["price_concept"].to(device)
            location_concept = batch["location_concept"].to(device)
            menu_variety_concept = batch["menu_variety_concept"].to(device)
            waiting_time_concept = batch["waiting_time_concept"].to(device)
            waiting_area_concept = batch["waiting_area_concept"].to(device)                
        concept_labels=batch["concept_labels"].to(device)
        concept_labels = torch.t(concept_labels)
        concept_labels = concept_labels.contiguous().view(-1) 


        optimizer.zero_grad()
        outputs = model(input_ids=input_ids, attention_mask=attention_mask)
        if model_name == 'lstm':
            pooled_output = outputs
        else:
            pooled_output = outputs.last_hidden_state.mean(1)  
        outputs  = ModelXtoCtoY_layer(pooled_output)  
        XtoC_output = outputs [1:] 
        XtoY_output = outputs [0:1]
        # XtoC_loss
        XtoC_logits = torch.nn.Sigmoid()(torch.cat(XtoC_output, dim=0)) # 32*4 00000000111111112222222233333333
        XtoC_loss = loss_fn(XtoC_logits, concept_labels)
        # XtoY_loss
        XtoY_loss = loss_fn(XtoY_output[0], label)
        loss = XtoC_loss*lambda_XtoC+XtoY_loss
        loss.backward()
        optimizer.step()

    model.eval()
    ModelXtoCtoY_layer.eval()
    test_accuracy = 0.
    concept_test_accuracy = 0.
    predict_labels = np.array([])
    true_labels = np.array([])
    concept_predict_labels = np.array([])
    concept_true_labels = np.array([])
    predict_concepts = []

    with torch.no_grad():
        for batch in tqdm(test_loader, desc="Test", unit="batch"):
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            label = batch["label"].to(device)
            food_concept = batch["food_concept"].to(device)
            ambiance_concept=batch["ambiance_concept"].to(device)
            service_concept=batch["service_concept"].to(device)
            noise_concept=batch["noise_concept"].to(device)
            if data_type != "pure_cebab":
                cleanliness_concept = batch["cleanliness_concept"].to(device)
                price_concept = batch["price_concept"].to(device)
                location_concept = batch["location_concept"].to(device)
                menu_variety_concept = batch["menu_variety_concept"].to(device)
                waiting_time_concept = batch["waiting_time_concept"].to(device)
                waiting_area_concept = batch["waiting_area_concept"].to(device)        
            concept_labels=batch["concept_labels"].to(device)
            concept_labels = torch.t(concept_labels)
            concept_labels = concept_labels.contiguous().view(-1)


            outputs = model(input_ids=input_ids, attention_mask=attention_mask)
            if model_name == 'lstm':
                pooled_output = outputs
            else:
                pooled_output = outputs.last_hidden_state.mean(1)  
            outputs = ModelXtoCtoY_layer(pooled_output)  
            XtoC_output = outputs [1:] 
            XtoY_output = outputs [0:1]         
            predictions = torch.argmax(XtoY_output[0], axis=1)
            test_accuracy += torch.sum(predictions == label).item()
            predict_labels = np.append(predict_labels, predictions.cpu().numpy())
            true_labels = np.append(true_labels, label.cpu().numpy())
            #concept accuracy
            XtoC_logits = torch.cat(XtoC_output, dim=0)
            concept_predictions = torch.argmax(XtoC_logits, axis=1)
            concept_test_accuracy += torch.sum(concept_predictions == concept_labels).item()
            concept_predict_labels = np.append(concept_predict_labels, concept_predictions.cpu().numpy())
            concept_true_labels = np.append(concept_true_labels, concept_labels.cpu().numpy())
            concept_predictions = concept_predictions.reshape(-1,num_concept_labels)  # reshape 二维向量[batch_size*num_concept_labels]
        
        test_accuracy /= len(test_dataset)
        num_labels = len(np.unique(true_labels))

        concept_test_accuracy /= len(test_dataset)
        concept_num_true_labels = len(np.unique(concept_true_labels))
        
        macro_f1_scores = []
        for label in range(num_labels):
            label_pred = np.array(predict_labels) == label
            label_true = np.array(true_labels) == label
            macro_f1_scores.append(f1_score(label_true, label_pred, average='macro'))
            mean_macro_f1_score = np.mean(macro_f1_scores)

        concept_macro_f1_scores = []
        for concept_label in range(concept_num_true_labels):
            concept_label_pred = np.array(concept_predict_labels) == concept_label
            concept_label_true = np.array(concept_true_labels) == concept_label
            concept_macro_f1_scores.append(f1_score(concept_label_true, concept_label_pred, average='macro'))
            concept_mean_macro_f1_score = np.mean(concept_macro_f1_scores)

    print(f"Epoch {epoch + 1}: Test concept Acc = {concept_test_accuracy*100/num_concept_labels} Test concept Macro F1 = {concept_mean_macro_f1_score*100}")
    print(f"Epoch {epoch + 1}: Test Acc = {test_accuracy*100} Test Macro F1 = {mean_macro_f1_score*100}")
