#!/usr/bin/env python
# _*_ coding: utf-8 _*_
# @Time : 2025/10/17 下午9:11 
# @Author : Huzhaojun
# @Version：V 1.0
# @File : emailServer.py
# @desc : README.md

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
# from backend.core.logger import Config, setup_logger

# 加载配置和安装记录器
# config = Config("./config.yaml")
# logger = setup_logger(config)

class EmilServer:

    def __init__(self):
        self.mail_host = 'smtp.qq.com'
        self.mail_port = 465
        self.mail_pwd = 'qilbnbisolrbdafd'
        self.sender = 'radishtools@foxmail.com'
        self.receivers = []

    def create_mime(self,
                    context: str,
                    receivers: list,
                    title: str = "title",
                    usertype: str = "plain",
                    charset: str = "utf-8",
                    batch_seng: bool = False
                    ) -> MIMEText | None:
        """
        初始换一个mimeText对象

        :param context: 正文内容, 必填
        :param receivers: 接受方， 必填
        :param title: 标题，不填默认 title
        :param usertype: 用户类型，默认 plain
        :param charset: 编码格式，默认指定 utf-8
        :param batch_seng: 群发，默认为 False
        :return: MIMEText对象
        """

        self.receivers.extend(receivers)

        message = MIMEText(context, usertype, charset)
        message['Subject'] = title
        # 发送方
        message['From'] = self.sender
        # 接收方
        message['To'] = self.receivers if batch_seng else self.receivers[0]

        return message

    def login_server(self):
        """
        登录服务器
        :return:
        """

        smtp_obj = smtplib.SMTP()
        smtp_obj.connect(self.mail_host, 25)
        smtp_obj.login(self.sender, self.mail_pwd)
        return smtp_obj

    def init_ssl_smtp(self):
        """
        通过ssl登录

        :return:
        """

        smtp = smtplib.SMTP_SSL(self.mail_host, self.mail_port)
        smtp.login(self.sender, self.mail_pwd)
        return smtp

    def send_info(self, message: MIMEText, smtp_obj: smtplib.SMTP or smtplib.SMTP_SSL):
        """
        发送信息

        :param message:
        :param smtp_obj:
        :return:
        """

        try:
            if message and smtp_obj:
                req = smtp_obj.sendmail(
                    self.sender, self.receivers, message.as_string()
                )

                print(f"EmailServer: 信息发送成功 req:{req}")

        except Exception as e:
            print(f"EmailServerError: {e}")

        finally:
            if smtp_obj:
                smtp_obj.quit()
                print(f"EmailServer quit success")
