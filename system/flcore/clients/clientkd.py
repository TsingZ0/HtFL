import copy
import torch
import torch.nn as nn
import numpy as np
import time
import torch.nn.functional as F
from flcore.clients.clientbase import Client, load_item, save_item


class clientKD(Client):
    def __init__(self, args, id, train_samples, test_samples, **kwargs):
        super().__init__(args, id, train_samples, test_samples, **kwargs)

        self.mentee_learning_rate = args.mentee_learning_rate

        if args.save_folder_name == 'temp' or 'temp' not in args.save_folder_name:
            W_h = nn.Linear(self.feature_dim, self.feature_dim, bias=False).to(self.device)
            save_item(W_h, self.role, 'W_h', self.save_folder_name)
            global_model = load_item('Server', 'global_model', self.save_folder_name)
            save_item(global_model, self.role, 'global_model', self.save_folder_name)

        self.KL = nn.KLDivLoss()
        self.MSE = nn.MSELoss()


    def train(self):
        trainloader = self.load_train_data()
        model = load_item(self.role, 'model', self.save_folder_name)
        global_model = load_item(self.role, 'global_model', self.save_folder_name)
        W_h = load_item(self.role, 'W_h', self.save_folder_name)
        optimizer = torch.optim.SGD(model.parameters(), lr=self.learning_rate)
        optimizer_g = torch.optim.SGD(global_model.parameters(), lr=self.learning_rate)
        optimizer_W = torch.optim.SGD(W_h.parameters(), lr=self.learning_rate)
        # model.to(self.device)
        model.train()
        
        start_time = time.time()

        max_local_epochs = self.local_epochs
        if self.train_slow:
            max_local_epochs = np.random.randint(1, max_local_epochs // 2)

        for step in range(max_local_epochs):
            for i, (x, y) in enumerate(trainloader):
                if type(x) == type([]):
                    x[0] = x[0].to(self.device)
                else:
                    x = x.to(self.device)
                y = y.to(self.device)
                if self.train_slow:
                    time.sleep(0.1 * np.abs(np.random.rand()))
                rep = model.base(x)
                rep_g = global_model.base(x)
                output = model.head(rep)
                output_g = global_model.head(rep_g)

                CE_loss = self.loss(output, y)
                CE_loss_g = self.loss(output_g, y)
                L_d = self.KL(F.log_softmax(output, dim=1), F.softmax(output_g, dim=1)) / (CE_loss + CE_loss_g)
                L_d_g = self.KL(F.log_softmax(output_g, dim=1), F.softmax(output, dim=1)) / (CE_loss + CE_loss_g)
                L_h = self.MSE(rep, W_h(rep_g)) / (CE_loss + CE_loss_g)
                L_h_g = self.MSE(rep, W_h(rep_g)) / (CE_loss + CE_loss_g)

                loss = CE_loss + L_d + L_h
                loss_g = CE_loss_g + L_d_g + L_h_g

                optimizer.zero_grad()
                optimizer_g.zero_grad()
                optimizer_W.zero_grad()
                loss.backward(retain_graph=True)
                loss_g.backward()
                # prevent divergency on specifical tasks
                torch.nn.utils.clip_grad_norm_(model.parameters(), 10)
                torch.nn.utils.clip_grad_norm_(global_model.parameters(), 10)
                torch.nn.utils.clip_grad_norm_(W_h.parameters(), 10)
                optimizer.step()
                optimizer_g.step()
                optimizer_W.step()

        save_item(model, self.role, 'model', self.save_folder_name)
        save_item(global_model, self.role, 'global_model', self.save_folder_name)
        save_item(W_h, self.role, 'W_h', self.save_folder_name)

        self.train_time_cost['num_rounds'] += 1
        self.train_time_cost['total_cost'] += time.time() - start_time

        
    def set_parameters(self):
        global_model = load_item(self.role, 'global_model', self.save_folder_name)
        compressed_param = load_item('Server', 'compressed_param', self.save_folder_name)
        compressed_param = recover(compressed_param)
        for name, old_param in global_model.named_parameters():
            if name in compressed_param:
                old_param.data = torch.tensor(compressed_param[name], device=self.device).data.clone()
        save_item(global_model, self.role, 'global_model', self.save_folder_name)

    def train_metrics(self):
        trainloader = self.load_train_data()
        model = load_item(self.role, 'model', self.save_folder_name)
        global_model = load_item(self.role, 'global_model', self.save_folder_name)
        W_h = load_item(self.role, 'W_h', self.save_folder_name)
        # model.to(self.device)
        model.eval()

        train_num = 0
        losses = 0
        with torch.no_grad():
            for x, y in trainloader:
                if type(x) == type([]):
                    x[0] = x[0].to(self.device)
                else:
                    x = x.to(self.device)
                y = y.to(self.device)
                rep = model.base(x)
                rep_g = global_model.base(x)
                output = model.head(rep)
                output_g = global_model.head(rep_g)

                CE_loss = self.loss(output, y)
                CE_loss_g = self.loss(output_g, y)
                L_d = self.KL(F.log_softmax(output, dim=1), F.softmax(output_g, dim=1)) / (CE_loss + CE_loss_g)
                L_h = self.MSE(rep, W_h(rep_g)) / (CE_loss + CE_loss_g)

                loss = CE_loss + L_d + L_h
                train_num += y.shape[0]
                losses += loss.item() * y.shape[0]

        return losses, train_num
            

def recover(compressed_param):
    for k in compressed_param.keys():
        if len(compressed_param[k]) == 3:
            # use np.matmul to support high-dimensional CNN param
            compressed_param[k] = np.matmul(
                compressed_param[k][0] * compressed_param[k][1][..., None, :], 
                    compressed_param[k][2])
    return compressed_param