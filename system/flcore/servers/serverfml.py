import copy
import random
import time
from flcore.clients.clientfml import clientFML
from flcore.servers.serverbase import Server
from threading import Thread

import torchvision
from flcore.trainmodel.models import *
from flcore.trainmodel.resnet import resnet18, resnet10, resnet8, resnet6, resnet4

# hyper-params for Text tasks
vocab_size = 98635
max_len=200
hidden_dim=32


class FML(Server):
    def __init__(self, args, times):
        super().__init__(args, times)
        args.global_model = eval(args.models[0])
        args.global_model.fc = nn.AdaptiveAvgPool1d(args.feature_dim)
        head = nn.Linear(args.feature_dim, args.num_classes)
        args.global_model = BaseHeadSplit(args.global_model, head).to(args.device)
        
        self.global_model = copy.deepcopy(args.global_model)
        
        # select slow clients
        self.set_slow_clients()
        self.set_clients(clientFML)

        print(f"\nJoin ratio / total clients: {self.join_ratio} / {self.num_clients}")
        print("Finished creating server and clients.")

        # self.load_model()
        self.Budget = []


    def train(self):
        for i in range(self.global_rounds+1):
            s_t = time.time()
            self.selected_clients = self.select_clients()
            self.send_models()

            if i%self.eval_gap == 0:
                print(f"\n-------------Round number: {i}-------------")
                print("\nEvaluate personalized models")
                self.evaluate()

            for client in self.selected_clients:
                client.train()

            # threads = [Thread(target=client.train)
            #            for client in self.selected_clients]
            # [t.start() for t in threads]
            # [t.join() for t in threads]

            self.receive_models()
            self.aggregate_parameters()

            self.Budget.append(time.time() - s_t)
            print('-'*25, 'time cost', '-'*25, self.Budget[-1])

            if self.auto_break and self.check_done(acc_lss=[self.rs_test_acc], top_cnt=self.top_cnt):
                break

        print("\nBest accuracy.")
        # self.print_(max(self.rs_test_acc), max(
        #     self.rs_train_acc), min(self.rs_train_loss))
        print(max(self.rs_test_acc))
        print("\nAverage time cost per round.")
        print(sum(self.Budget[1:])/len(self.Budget[1:]))

        self.save_results()
        self.save_global_model()
        

    def receive_models(self):
        assert (len(self.selected_clients) > 0)

        active_clients = random.sample(
            self.selected_clients, int((1-self.client_drop_rate) * self.current_num_join_clients))

        self.uploaded_ids = []
        self.uploaded_models = []
        for client in active_clients:
            try:
                client_time_cost = client.train_time_cost['total_cost'] / client.train_time_cost['num_rounds'] + \
                        client.send_time_cost['total_cost'] / client.send_time_cost['num_rounds']
            except ZeroDivisionError:
                client_time_cost = 0
            if client_time_cost <= self.time_threthold:
                self.uploaded_ids.append(client.id)
                self.uploaded_models.append(client.global_model)

    def aggregate_parameters(self):
        assert (len(self.uploaded_models) > 0)

        self.global_model = copy.deepcopy(self.uploaded_models[0])
        for param in self.global_model.parameters():
            param.data.zero_()
            
        # use 1/len(self.uploaded_models) as the weight for privacy and fairness
        for client_model in self.uploaded_models:
            self.add_parameters(1/len(self.uploaded_models), client_model)
