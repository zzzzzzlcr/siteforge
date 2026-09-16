package internal

import (
	"os"
	"github.com/chromedp/cdproto/page"
	"testing"
	"time"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
)

func TestSnapshotBDD(t *testing.T) {
	RegisterFailHandler(Fail)
	RunSpecs(t, "Snapshot Frame Enhancement Suite")
}

var _ = Describe("snapshot frame enhancement", func() {
	var client *Client
	var host string
	var port int

	BeforeEach(func() {
		host = os.Getenv("CDP_HOST")
		if host == "" { host = "127.0.0.1" }
		port = 9222
	})

	Describe("GetFrameContent", func() {
		It("reads content via isolatedWorld", func() {
			pages, err := ListPageTargets(host, port, false)
			if err != nil || len(pages) == 0 { Skip("Chrome not available") }

			client, err = NewClient(host, port)
			Expect(err).NotTo(HaveOccurred())
			defer client.Disconnect()

			client.Navigate("http://localhost:8080/mui-datepicker", "")
			ft, err := client.GetFrameTree()
			Expect(err).NotTo(HaveOccurred())

			body, method, err := client.GetFrameContent(ft.Frame.ID)
			Expect(err).NotTo(HaveOccurred())
			Expect(method).To(Equal("isolatedWorld"))
			Expect(body).NotTo(BeNil())
		})
	})

	Describe("nested iframes", func() {
		It("discovers nested iframes via DOM with proper hierarchy", func() {
			pages, err := ListPageTargets(host, port, false)
			if err != nil || len(pages) == 0 { Skip("Chrome not available") }

			client, err = NewClient(host, port)
			Expect(err).NotTo(HaveOccurred())
			defer client.Disconnect()

			// Create nested iframes using srcdoc (synchronous,
			// doesn't rely on onload callback timing)
			client.EvalInFrame("", `(function(){
				document.body.innerHTML = '<iframe name="outer-frame" srcdoc="<html><body><iframe name=\'inner-frame\' src=\'about:blank\'></iframe></body></html>" style="width:400px;height:300px;"></iframe>';
				return 'added';
			})()`, nil)

			ft, err := client.GetFrameTreeWithEvents(4 * time.Second)
			Expect(err).NotTo(HaveOccurred())

			// Find outer iframe in tree
			var outerChild *page.FrameTree
			for _, child := range ft.ChildFrames {
				if child != nil && child.Frame.Name == "outer-frame" {
					outerChild = child
					break
				}
			}
			Expect(outerChild).NotTo(BeNil(), "outer iframe should be in frame tree")

			// Inner iframe should be a child of outer iframe
			found := false
			for _, inner := range outerChild.ChildFrames {
				if inner != nil && inner.Frame.Name == "inner-frame" {
					found = true
					break
				}
			}
			Expect(found).To(BeTrue(), "nested inner iframe should be child of outer iframe")
		})
	})

	Describe("CaptureFrame", func() {
		It("returns single frame without tree wrapper", func() {
			pages, err := ListPageTargets(host, port, false)
			if err != nil || len(pages) == 0 { Skip("Chrome not available") }

			client, err = NewClient(host, port)
			Expect(err).NotTo(HaveOccurred())
			defer client.Disconnect()

			client.Navigate("http://localhost:8080/mui-datepicker", "")
			ft, err := client.GetFrameTree()
			Expect(err).NotTo(HaveOccurred())

			snap, method, err := client.CaptureFrame(ft.Frame)
			Expect(err).NotTo(HaveOccurred())
			Expect(snap.FrameID).To(Equal(string(ft.Frame.ID)))
			Expect(method).NotTo(BeEmpty())
			Expect(snap.Title).NotTo(BeEmpty())
		})

		It("reports method field for content source", func() {
			pages, err := ListPageTargets(host, port, false)
			if err != nil || len(pages) == 0 { Skip("Chrome not available") }

			client, err = NewClient(host, port)
			Expect(err).NotTo(HaveOccurred())
			defer client.Disconnect()

			client.Navigate("http://localhost:8080/mui-datepicker", "")
			ft, err := client.GetFrameTree()
			Expect(err).NotTo(HaveOccurred())

			snap, method, _ := client.CaptureFrame(ft.Frame)
			Expect(method).To(Equal("isolatedWorld"))
			Expect(snap.Method).To(Equal("isolatedWorld"))
			Expect(snap.Error).To(BeEmpty())
		})
	})
})
