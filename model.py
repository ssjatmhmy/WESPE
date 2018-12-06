import os
import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision.models import vgg19
import torch.optim as optim


class ConvBlock(nn.Module):

    def __init__(self):
        super(ConvBlock, self).__init__()
        self.conv1 = nn.Conv2d(64, 64, 3, padding=1)
        self.conv2 = nn.Conv2d(64, 64, 3, padding=1)
        self.instance_norm1 = nn.InstanceNorm2d(64, affine=True)
        self.instance_norm2 = nn.InstanceNorm2d(64, affine=True)
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x):
        y = self.relu(self.instance_norm1(self.conv1(x)))
        y = self.relu(self.instance_norm2(self.conv2(y))) + x
        return y


class GaussianBlur(nn.Module):

    def __init__(self):
        super(GaussianBlur, self).__init__()
        kernel = [
            [0.03797616, 0.044863533, 0.03797616],
            [0.044863533, 0.053, 0.044863533],
            [0.03797616, 0.044863533, 0.03797616]
        ]
        kernel = torch.Tensor(kernel).unsqueeze(0).unsqueeze(0)
        self.weight = nn.Parameter(data=kernel, requires_grad=False)

    def forward(self, x):
        x1 = x[:, 0].unsqueeze(1)
        x2 = x[:, 1].unsqueeze(1)
        x3 = x[:, 2].unsqueeze(1)
        x1 = F.conv2d(x1, self.weight, padding=1)
        x2 = F.conv2d(x2, self.weight, padding=1)
        x3 = F.conv2d(x3, self.weight, padding=1)
        x = torch.cat([x1, x2, x3], dim=1)
        return x


class GrayLayer(nn.Module):

    def __init__(self):
        super(GrayLayer, self).__init__()

    def forward(self, x):
        result = 0.299 * x[:, 0] + 0.587 * x[:, 1] + 0.114 * x[:, 2]
        return result.unsqueeze(1)


class Generator(nn.Module):

    def __init__(self):
        super(Generator, self).__init__()
        self.conv1 = nn.Conv2d(3, 64, 9, padding=4)
        self.blocks = nn.Sequential(
            ConvBlock(),
            ConvBlock(),
            ConvBlock(),
            ConvBlock(),
        )
        self.conv2 = nn.Conv2d(64, 64, 3, padding=1)
        self.conv3 = nn.Conv2d(64, 64, 3, padding=1)
        self.conv4 = nn.Conv2d(64, 3, 9, padding=4)

    def forward(self, x):
        """
        Arguments:
            x: a float tensor with shape [b, 3, h, w].
            It represents a RGB image with pixel values in [0, 1] range.
        Returns:
            a float tensor with shape [b, 3, h, w].
            It represents a RGB image with pixel values in [0, 1] range.
        """
        x = F.relu(self.conv1(x))
        x = self.blocks(x)
        x = F.relu(self.conv2(x))
        x = F.relu(self.conv3(x))
        x = F.tanh(self.conv4(x)) * 0.5 + 0.5
        return x


class Discriminator(nn.Module):

    def __init__(self, num_input_channels):
        super(Discriminator, self).__init__()
        self.conv_layers = nn.Sequential(
            nn.Conv2d(num_input_channels, 48, 11, stride=4, padding=5),
            nn.LeakyReLU(negative_slope=0.2, inplace=True),
            nn.Conv2d(48, 128, 5, stride=2, padding=2),
            nn.LeakyReLU(negative_slope=0.2, inplace=True),
            nn.InstanceNorm2d(128, affine=True),
            nn.Conv2d(128, 192, 3, stride=1, padding=1),
            nn.LeakyReLU(negative_slope=0.2, inplace=True),
            nn.InstanceNorm2d(192, affine=True),
            nn.Conv2d(192, 192, 3, stride=1, padding=1),
            nn.LeakyReLU(negative_slope=0.2, inplace=True),
            nn.InstanceNorm2d(192, affine=True),
            nn.Conv2d(192, 128, 3, stride=2, padding=1),
            nn.LeakyReLU(negative_slope=0.2, inplace=True),
            nn.InstanceNorm2d(128, affine=True),
        )
        self.fc = nn.Linear(128 * 4 * 4, 1024)
        self.out = nn.Linear(1024, 1)

    def forward(self, x):
        """
        Arguments:
            x: a float tensor with shape [b, num_input_channels, h, w].
            It has values in [0, 1] range. And h = w = 64.
        Returns:
            a float tensor with shape [b].
        """
        b = x.size(0)
        x = self.conv_layers(x)
        x = x.view(b, 128 * 4 * 4)
        x = F.leaky_relu(self.fc(x), negative_slope=0.2)
        x = self.out(x).view(b)
        return x


class VGG(nn.Module):

    def __init__(self):
        super(VGG, self).__init__()
        self.model = vgg19(pretrained=True).features[:-1]
        self.mean = torch.Tensor([0.485, 0.456, 0.406]).cuda().view(1, 3, 1, 1)
        self.std = torch.Tensor([0.229, 0.224, 0.225]).cuda().view(1, 3, 1, 1)
        for param in self.model.parameters():
            param.requires_grad = False

    def forward(self, x):
        """
        Arguments:
            x: a float tensor with shape [b, 3, h, w].
            It represents a RGB image with pixel values in [0, 1] range.
        Returns:
            a float tensor with shape [b, 512, h/16, w/16].
        """
        x = (x - self.mean)/self.std
        x = self.model(x)  # relu_5_4 features
        return x


class TVLoss(nn.Module):

    def __init__(self):
        super(TVLoss, self).__init__()

    def forward(self, x):
        """
        Arguments:
            x: a float tensor with shape [b, 3, h, w].
            It represents a RGB image with pixel values in [0, 1] range.
        Returns:
            a float tensor with shape [].
        """

        batch_size, c, h, w = x.size()
        count_h = c * (h - 1) * w
        count_w = c * h * (w - 1)
        h_tv = torch.pow((x[:, :, 1:, :] - x[:, :, :(h - 1), :]), 2).sum()
        w_tv = torch.pow((x[:, :, :, 1:] - x[:, :, :, :(w - 1)]), 2).sum()
        return 2.0*(h_tv/count_h + w_tv/count_w)/batch_size


class WESPE:

    def __init__(self):

        self.generator_g = Generator().cuda()
        self.generator_f = Generator().cuda()
        self.discriminator_c = Discriminator(num_input_channels=3).cuda()
        self.discriminator_t = Discriminator(num_input_channels=1).cuda()

        self.content_criterion = lambda x, y: ((x - y)**2).sum()/x.numel()
        self.tv_criterion = TVLoss().cuda()
        self.color_criterion = nn.BCEWithLogitsLoss().cuda()
        self.texture_criterion = nn.BCEWithLogitsLoss().cuda()

        self.g_optimizer = optim.Adam(lr=1e-4, params=self.generator_g.parameters())
        self.f_optimizer = optim.Adam(lr=1e-4, params=self.generator_f.parameters())
        self.t_optimizer = optim.Adam(lr=1e-4, params=self.discriminator_t.parameters())
        self.c_optimizer = optim.Adam(lr=1e-4, params=self.discriminator_c.parameters())

        self.vgg = VGG().cuda()
        self.blur = GaussianBlur().cuda()
        self.gray = GrayLayer().cuda()

    def train_step(self, x, y):

        batch_size = x.size(0)

        y_fake = self.generator_g(x)
        x_fake = self.generator_f(y_fake)

        self.g_optimizer.zero_grad()
        self.f_optimizer.zero_grad()

        # content loss
        vgg_x_true = self.vgg(x).detach()
        vgg_x_fake = self.vgg(x_fake)
        content_loss = self.content_criterion(vgg_x_fake, vgg_x_true)

        # tv loss
        _, c, h, w = y_fake.size()
        tv_loss = 1.0/(c * h * w) * self.tv_criterion(y_fake)

        pos_labels = torch.ones(batch_size, dtype=torch.long, device=x.device)
        neg_labels = torch.zeros(batch_size, dtype=torch.long, device=x.device)

        y_fake_blur = self.blur(y_fake)
        y_real_blur = self.blur(y)

        y_fake_blur_dc_pred = self.discriminator_c(y_fake_blur)
        y_real_blur_dc_pred = self.discriminator_c(y_real_blur)
        gen_dc_loss = self.color_criterion(y_fake_blur_dc_pred, pos_labels)

        y_fake_gray = self.gray(y_fake)
        y_real_gray = self.gray(y)

        y_fake_gray_dt_pred = self.discriminator_t(y_fake_gray)
        y_real_gray_dt_pred = self.discriminator_t(y_real_gray)
        gen_dt_loss = self.texture_criterion(y_fake_gray_dt_pred, pos_labels)

        generator_loss = content_loss + 10.0 * tv_loss + (gen_dc_loss + gen_dt_loss) * 5e-3

        generator_loss.backward()
        self.g_optimizer.step()
        self.f_optimizer.step()

        self.c_optimizer.zero_grad()
        self.t_optimizer.zero_grad()

        y_fake_blur_dc_pred = self.discriminator_c(y_fake_blur.detach())
        dc_loss = self.color_criterion(y_fake_blur_dc_pred, neg_labels) \
            + self.color_criterion(y_real_blur_dc_pred, pos_labels)

        y_fake_gray_dt_pred = self.discriminator_t(y_fake_gray.detach())
        dt_loss = self.texture_criterion(y_fake_gray_dt_pred, neg_labels) \
            + self.texture_criterion(y_real_gray_dt_pred, pos_labels)

        discriminator_loss = (dt_loss + dc_loss) * 5e-3

        discriminator_loss.backward()
        self.c_optimizer.step()
        self.t_optimizer.step()

        loss_dict = {
            "content": content_loss.item(),
            "tv": tv_loss.item(),
            "gen_dc": gen_dc_loss.item(),
            "gen_dt": gen_dt_loss.item(),
            "texture_loss": dt_loss.item(),
            "color_loss": dc_loss.item()
        }

        return loss_dict

    def save_model(self, model_path):
        torch.save(self.generator_f.state_dict(), model_path+'_generator_f.pth')
        torch.save(self.generator_g.state_dict(), model_path+'_generator_g.pth')
        torch.save(self.discriminator_t.state_dict(), model_path+'_discriminator_t.pth')
        torch.save(self.discriminator_c.state_dict(), model_path+'_discriminator_c.pth')

    def load_model(self, model_path, only_gen):
        self.generator_f.load_state_dict(torch.load(model_path+'_generator_f.pth'))
        self.generator_g.load_state_dict(torch.load(model_path+'_generator_g.pth'))
        if not only_gen:
            self.discriminator_c.load_state_dict(torch.load(model_path + '_discriminator_c.pth'))
            self.discriminator_t.load_state_dict(torch.load(model_path + '_discriminator_t.pth'))

    def inference(self, x):

        return self.generator_g(x)