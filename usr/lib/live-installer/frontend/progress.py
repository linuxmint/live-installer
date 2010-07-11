try:
	import pygtk
	pygtk.require("2.0")
	import gtk
	import gobject
except Exception, detail:
	print detail
	
class ProgressDialog:
	
	def __init__(self):
		self.glade = '/usr/share/live-installer/interface.glade'
		self.dTree = gtk.glade.XML(self.glade, 'progress_window')
		self.window = self.dTree.get_widget('progress_window')
		self.progressbar = self.dTree.get_widget('progressbar_operation')
		self.label = self.dTree.get_widget('label_operation')
		self.should_pulse = False
		
	def show(self, label=None, title=None):
		def pbar_pulse():
			if(not self.should_pulse):
				return False
			self.progressbar.pulse()
			return self.should_pulse
		if(label is not None):
			self.label.set_markup(label)
		if(title is not None):
			self.window.set_title(title)
		self.should_pulse = True
		self.window.show_all()
		gobject.timeout_add(100, pbar_pulse)
		
	def hide(self):
		self.should_pulse = False
		self.window.hide()	
