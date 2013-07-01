"""Implementation of the read-only Diagnostics UI."""

__author__ = 'anandkhare@google.com (Anand Khare)'

import google3
import tornado.ioloop
import tornado.web


# If RG's connectivity to cloud is healthy, redirect to fiber.google.com
# TODO(anandkhare): conditionally redirect only when needed
class MainHandler(tornado.web.RequestHandler):
  def get(self):    # pylint: disable=g-bad-name
    self.redirect('https://fiber.google.com')


# If no connectivity, display local diagnostics UI
class DiagnosticsHandler(tornado.web.RequestHandler):
  def get(self):    # pylint: disable=g-bad-name
    self.render('template.html')


settings = {
    'static_path': 'static',
    'template_path': '.',
}


application = tornado.web.Application([
    (r'/', MainHandler),
    (r'/diagnostics', DiagnosticsHandler),
], **settings)

# Listen for requests at port 8880
if __name__ == '__main__':
  application.listen(8880)
  tornado.ioloop.IOLoop.instance().start()
